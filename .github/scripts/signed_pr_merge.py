#!/usr/bin/env python3
"""Land bot-generated files on a protected default branch.

The riscv org ruleset "Enforce DCO Compliance." guards the default branch of
these repos with two rules, and a scheduled job can satisfy neither the obvious
way:

  * required_status_checks -> "DCO". The DCO app only reports its check on
    pull_request events, so a direct push leaves the context unreported and
    GitHub holds it as "expected" forever.
  * required_signatures. Commits made by `git commit` on a runner are unsigned,
    and GitHub refuses to merge a PR that *contains* an unsigned commit --
    squashing does not launder it.

So this script:

  1. creates a branch off the base branch head;
  2. writes the commit with the GraphQL createCommitOnBranch mutation, which
     GitHub signs with its own web-flow key (the REST contents API does *not*
     sign, and passing an explicit author to it suppresses signing entirely);
  3. opens a PR so the DCO app actually runs;
  4. waits for the DCO check, then squash-merges.

The commit message carries a Signed-off-by trailer naming the commit author.
The author is whoever owns GITHUB_TOKEN, so the trailer is verified against the
created commit and the run fails loudly on a mismatch rather than letting DCO
reject it for a reason that is hard to read from the log.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
# Identity GitHub stamps on commits authored with a workflow's GITHUB_TOKEN.
DEFAULT_SIGNOFF_NAME = "github-actions[bot]"
DEFAULT_SIGNOFF_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
# Real check-run conclusions. Anything else means "not reported yet".
CONCLUSIONS = {
    "success", "failure", "neutral", "cancelled",
    "timed_out", "action_required", "stale", "skipped",
}


class ApiError(RuntimeError):
    pass


def call(path: str, token: str, method: str = "GET", body: dict | None = None):
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise ApiError(f"{method} {url} -> {exc.code}: {detail}") from None


def graphql(query: str, variables: dict, token: str):
    out = call("https://api.github.com/graphql", token, "POST",
               {"query": query, "variables": variables})
    if out.get("errors"):
        raise ApiError(f"GraphQL: {json.dumps(out['errors'])}")
    return out["data"]


MUTATION = """
mutation($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) {
    commit {
      oid
      author { name email }
      signature { isValid state }
    }
  }
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", action="append", required=True,
                    help="Repo-relative file to commit. Repeatable.")
    ap.add_argument("--message", required=True, help="Commit/PR headline.")
    ap.add_argument("--body", default="", help="Commit message body.")
    ap.add_argument("--pr-body", default="", help="PR description.")
    ap.add_argument("--branch-prefix", default="bot/data-refresh")
    ap.add_argument("--base", default="",
                    help="Branch to land on. Defaults to the repo's default "
                         "branch; override only for testing.")
    ap.add_argument("--signoff-name", default=DEFAULT_SIGNOFF_NAME)
    ap.add_argument("--signoff-email", default=DEFAULT_SIGNOFF_EMAIL)
    ap.add_argument("--timeout", type=int, default=120,
                    help="Seconds to wait for the DCO check.")
    args = ap.parse_args()

    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    # Always target the repo's default branch, never GITHUB_REF_NAME: a run
    # dispatched from a feature branch must still land the data on the default
    # branch, and targeting the ref would also quietly skip the branch rules
    # that this whole flow exists to satisfy.
    base = args.base or call(f"/repos/{repo}", token)["default_branch"]
    run_id = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
    branch = f"{args.branch_prefix}-{run_id}"

    signoff = f"Signed-off-by: {args.signoff_name} <{args.signoff_email}>"
    body = f"{args.body}\n\n{signoff}".strip() if args.body else signoff

    base_sha = call(f"/repos/{repo}/commits/{base}", token)["sha"]
    call(f"/repos/{repo}/git/refs", token, "POST",
         {"ref": f"refs/heads/{branch}", "sha": base_sha})
    print(f"Created {branch} at {base_sha[:8]}.")

    def drop_branch():
        try:
            call(f"/repos/{repo}/git/refs/heads/{branch}", token, "DELETE")
        except ApiError:
            pass

    try:
        additions = []
        for path in args.path:
            with open(path, "rb") as fh:
                additions.append({
                    "path": path,
                    "contents": base64.b64encode(fh.read()).decode(),
                })

        commit = graphql(MUTATION, {"input": {
            "branch": {"repositoryNameWithOwner": repo, "branchName": branch},
            "message": {"headline": args.message, "body": body},
            "fileChanges": {"additions": additions},
            "expectedHeadOid": base_sha,
        }}, token)["createCommitOnBranch"]["commit"]

        sig = commit.get("signature") or {}
        print(f"Committed {commit['oid'][:8]} "
              f"(signature {sig.get('state')}, valid={sig.get('isValid')}).")
        if not sig.get("isValid"):
            raise ApiError(
                f"Commit is not validly signed (state={sig.get('state')}); "
                "the required_signatures rule would reject the merge.")

        # The trailer must name the commit's own author or DCO rejects it.
        author = commit["author"]
        if author["email"] != args.signoff_email:
            raise ApiError(
                "Signed-off-by does not match the commit author. "
                f"author={author['name']} <{author['email']}> but trailer "
                f"said <{args.signoff_email}>. Re-run with "
                f"--signoff-name '{author['name']}' "
                f"--signoff-email '{author['email']}'.")
    except Exception:
        drop_branch()
        raise

    pr = call(f"/repos/{repo}/pulls", token, "POST", {
        "title": args.message,
        "head": branch,
        "base": base,
        "body": args.pr_body or body,
    })
    number = pr["number"]
    print(f"Opened PR #{number}.")

    def abandon(reason: str) -> int:
        print(f"::warning::{reason} Closing PR #{number}.")
        try:
            call(f"/repos/{repo}/pulls/{number}", token, "PATCH",
                 {"state": "closed"})
        finally:
            drop_branch()
        return 1

    deadline = time.time() + args.timeout
    conclusion = ""
    while time.time() < deadline:
        try:
            runs = call(f"/repos/{repo}/commits/{branch}/check-runs", token)
        except ApiError as exc:
            print(f"::warning::check-runs lookup failed: {exc}")
            time.sleep(5)
            continue
        dco = [c for c in runs.get("check_runs", []) if c["name"] == "DCO"]
        if dco and dco[0].get("conclusion") in CONCLUSIONS:
            conclusion = dco[0]["conclusion"]
            break
        time.sleep(5)

    if conclusion != "success":
        return abandon(
            f"DCO did not succeed on PR #{number} "
            f"(conclusion: {conclusion or 'not reported'}).")
    print("DCO passed.")

    last = ""
    for attempt in (1, 2, 3):
        try:
            call(f"/repos/{repo}/pulls/{number}/merge", token, "PUT",
                 {"merge_method": "squash"})
            print(f"Merged PR #{number} on attempt {attempt}.")
            drop_branch()
            return 0
        except ApiError as exc:
            last = str(exc)
            print(f"Merge attempt {attempt} failed: {exc}")
            time.sleep(10)

    # Persistent refusal usually means the base moved. The data is fully
    # regenerated every run, so abandon rather than resolving a conflict here;
    # the next scheduled run rebuilds from the new base.
    return abandon(f"Could not merge PR #{number}: {last}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ApiError as exc:
        print(f"::error::{exc}")
        sys.exit(1)
