"""
This script fetches data from a JIRA project and writes it to a CSV file.
The JIRA issues are retrieved using a JIRA Query Language (JQL) query,
and the data is written to a CSV file named with the current date and time.

Author: Rafael Sene, rafael@riscv.org - Initial implementation
"""

import base64
import csv
import os
import re
from datetime import date, datetime
from urllib.parse import urlparse

import requests
import yaml
from atlassian import Jira


GITHUB_API = "https://api.github.com"


def make_github_session():
    """Return an authenticated requests session for the GitHub API, or None.

    Uses GITHUB_TOKEN when available (raises the rate limit from 60 to 5000
    requests/hour). Works unauthenticated too, just more slowly.
    """
    session = requests.Session()
    session.headers.update({
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "riscv-adm-spec-dashboard",
    })
    token = os.getenv("GHTOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return session


def _gh_get(session, path, params=None):
    try:
        resp = session.get(f"{GITHUB_API}{path}", params=params, timeout=20)
    except requests.RequestException:
        return None
    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError:
            return None
    return None


def _commit_date(commit):
    """Pull the committer (fallback author) date out of a GitHub commit object."""
    if not isinstance(commit, dict):
        return ""
    inner = commit.get("commit") or {}
    committer = inner.get("committer") or {}
    if committer.get("date"):
        return committer["date"]
    author = inner.get("author") or {}
    return author.get("date") or ""


def parse_github_url(url):
    """Classify a GitHub URL into the shape we know how to resolve.

    Handles repo roots, pull requests, blob/tree (a path on a branch), and
    commit URLs. Returns None for anything that isn't a GitHub URL.
    """
    if not url:
        return None
    try:
        parsed = urlparse(str(url).strip())
    except ValueError:
        return None
    if "github.com" not in (parsed.netloc or "").lower():
        return None

    parts = [segment for segment in parsed.path.split("/") if segment]
    if len(parts) < 2:
        return None

    owner = parts[0]
    repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    rest = parts[2:]
    if not rest:
        return {"owner": owner, "repo": repo, "kind": "repo"}

    head = rest[0]
    if head == "pull" and len(rest) >= 2:
        return {"owner": owner, "repo": repo, "kind": "pr", "number": rest[1]}
    if head in ("tree", "blob") and len(rest) >= 2:
        return {
            "owner": owner,
            "repo": repo,
            "kind": "path",
            "branch": rest[1],
            "path": "/".join(rest[2:]),
        }
    if head == "commit" and len(rest) >= 2:
        return {"owner": owner, "repo": repo, "kind": "commit", "sha": rest[1]}
    return {"owner": owner, "repo": repo, "kind": "repo"}


def get_last_contribution(github_url, session):
    """Resolve the most recent code contribution for a spec's GitHub link.

    Returns a (iso_timestamp, source_label) tuple. Covers every way new code
    reaches a repo: pushes to any branch, open/merged pull requests, and
    commits touching a specific path on a branch. Returns ("", "") when there
    is no resolvable GitHub activity.
    """
    info = parse_github_url(github_url)
    if not info:
        return "", ""

    owner, repo, kind = info["owner"], info["repo"], info["kind"]

    if kind == "repo":
        data = _gh_get(session, f"/repos/{owner}/{repo}")
        if not data:
            return "", ""
        best = data.get("pushed_at") or ""
        source = "push" if best else ""
        # Catch fork-based PRs, whose commits don't move the base repo's
        # pushed_at, by checking the most recently updated open PR head.
        prs = _gh_get(
            session,
            f"/repos/{owner}/{repo}/pulls",
            {"state": "open", "sort": "updated", "direction": "desc", "per_page": 1},
        )
        if prs:
            pr = prs[0]
            sha = (pr.get("head") or {}).get("sha")
            commit = _gh_get(session, f"/repos/{owner}/{repo}/commits/{sha}") if sha else None
            cdate = _commit_date(commit)
            if cdate and cdate > best:
                best = cdate
                source = f"PR #{pr.get('number')}"
        return best, source

    if kind == "pr":
        number = info["number"]
        pr = _gh_get(session, f"/repos/{owner}/{repo}/pulls/{number}")
        if not pr:
            return "", ""
        if pr.get("merged_at"):
            return pr["merged_at"], f"PR #{number} merged"
        commits = _gh_get(
            session,
            f"/repos/{owner}/{repo}/pulls/{number}/commits",
            {"per_page": 100},
        )
        if commits:
            cdate = _commit_date(commits[-1])
            if cdate:
                return cdate, f"PR #{number} {pr.get('state', 'open')}"
        return pr.get("updated_at", ""), f"PR #{number}"

    if kind == "path":
        commits = _gh_get(
            session,
            f"/repos/{owner}/{repo}/commits",
            {"sha": info["branch"], "path": info["path"], "per_page": 1},
        )
        if commits:
            cdate = _commit_date(commits[0])
            if cdate:
                return cdate, f"branch:{info['branch']}"
        return "", ""

    if kind == "commit":
        commit = _gh_get(session, f"/repos/{owner}/{repo}/commits/{info['sha']}")
        cdate = _commit_date(commit)
        if cdate:
            return cdate, f"commit {info['sha'][:7]}"
        data = _gh_get(session, f"/repos/{owner}/{repo}")
        if data and data.get("pushed_at"):
            return data["pushed_at"], "push"
        return "", ""

    return "", ""


def extract_field_value(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        return value.get('value') or value.get('name') or value.get('label') or ""
    if isinstance(value, list):
        parts = [extract_field_value(item) for item in value]
        return ", ".join([part for part in parts if part])
    return str(value)


ARC_REVIEW_APPROVED_STATES = {
    "approved",
    "ar approved",
    "ar review not required",
    "approval not required",
    "not required",
    "done",
}

ARC_REVIEW_IN_PROGRESS_STATES = {
    "in progress",
    "in review",
    "under review",
    "ar review in progress",
}


FREEZE_ARC_REVIEW_PATTERN = re.compile(
    r"^\s*\[freeze\]\s*-\s*arc\s*review\b",
    re.IGNORECASE,
)

FAST_TRACK_PATTERN = re.compile(r"^\s*\[fast-?track\]", re.IGNORECASE)


def is_fast_track(subtasks):
    """Return True if any subtask is tagged with a `[Fast-Track]` prefix."""
    if not subtasks:
        return False
    for sub in subtasks:
        if not isinstance(sub, dict):
            continue
        sub_fields = sub.get('fields', {}) or {}
        summary = (sub_fields.get('summary') or "").strip()
        if FAST_TRACK_PATTERN.match(summary):
            return True
    return False


def extract_arc_review_status(subtasks):
    """Return the status of the `[Freeze] - ARC Review (required)` subtask.

    Only Freeze-phase ARC Review subtasks are considered — Plan/Development
    ARC Review subtasks are ignored. If a spec has more than one matching
    subtask (e.g. issuetype `ARC Review` plus a duplicate `Approval`),
    prefer an approved status; otherwise return the first match.
    """
    if not subtasks:
        return ""

    found_status = ""
    for sub in subtasks:
        if not isinstance(sub, dict):
            continue
        sub_fields = sub.get('fields', {}) or {}
        summary = (sub_fields.get('summary') or "").strip()
        if not FREEZE_ARC_REVIEW_PATTERN.match(summary):
            continue
        status_name = ((sub_fields.get('status') or {}).get('name') or "").strip()
        if status_name.lower() in ARC_REVIEW_APPROVED_STATES:
            return status_name
        if not found_status:
            found_status = status_name
    return found_status


def normalize_bod_report_value(value):
    text = extract_field_value(value).strip()
    if not text:
        return "No"
    lowered = text.lower()
    if lowered in ["yes", "true", "y", "1"]:
        return "Yes"
    if lowered in ["no", "false", "n", "0"]:
        return "No"
    return text


REQUIRED_FIELDS = [
    "summary",
    "status",
    "updated",
    "created",
    "subtasks",
    "customfield_10037",
    "customfield_10038",
    "customfield_10039",
    "customfield_10040",
    "customfield_10042",
    "customfield_10043",
    "customfield_10136",
    "customfield_10970",  # BoD Ratification Approval Baseline (schedule signal)
    "customfield_10989",  # BoD Ratification Approval Projection (schedule signal)
]

# ---- Ratification Progress computation (dashboard view; BoD Report cf10037 untouched) ----
GATE_KEYWORDS = ("approval", "vote", "ratification")
_DONE_STATUS_NAMES = ("done", "closed", "approved", "resolved", "ar review not required", "not required")


def _subtask_is_done(sub):
    st = (sub.get("fields") or {}).get("status") or {}
    cat = ((st.get("statusCategory") or {}).get("key") or "").lower()
    if cat:
        return cat == "done"
    return (st.get("name") or "").strip().lower() in _DONE_STATUS_NAMES


def _is_gate(summary):
    low = (summary or "").lower()
    return any(k in low for k in GATE_KEYWORDS)


def subtask_stats(subtasks):
    """Return (done, total, pct, work_open, gate_open) for a spec's subtasks.

    work_open  = open subtasks that are real work (not an approval/vote gate)
    gate_open  = open approval/vote subtasks (unassigned-by-design, expected)
    """
    subs = subtasks or []
    total = len(subs)
    done = work_open = gate_open = 0
    for s in subs:
        if _subtask_is_done(s):
            done += 1
        elif _is_gate((s.get("fields") or {}).get("summary")):
            gate_open += 1
        else:
            work_open += 1
    pct = round(100 * done / total) if total else 0
    return done, total, pct, work_open, gate_open


def _days_since(date_str):
    if not date_str:
        return None
    try:
        return (datetime.now() - datetime.strptime(str(date_str)[:10], "%Y-%m-%d")).days
    except ValueError:
        return None


def derive_ratification_progress(fields, work_open, gate_open, total, last_contribution):
    """Compute Ratification Progress from Jira + GitHub signals.

    Precedence: Completed > Awaiting Vote > Stalled > Exposed > On Track.
    'Watch' is intentionally never auto-set (human escalation only), so a
    manually-set Watch is preserved by the caller's change check.
    """
    status = (fields.get("status") or {}).get("name") or ""
    if status == "Specification Ratified":
        return "Completed"
    # all work subtasks done, only an approval/vote pending -> on track, not behind
    if total > 0 and work_open == 0 and gate_open >= 1:
        return "Awaiting Vote"
    # activity axis: real dev contribution (GitHub) or a recent Jira update
    contrib_age = _days_since(last_contribution)
    updated_age = _days_since(fields.get("updated"))
    active = (contrib_age is not None and contrib_age <= 90) or (updated_age is not None and updated_age <= 45)
    if not active:
        return "Stalled"
    # schedule axis: BoD projection later than baseline -> behind -> exposed
    base = fields.get("customfield_10970")
    proj = fields.get("customfield_10989")
    if base and proj and str(proj)[:10] > str(base)[:10]:
        return "Exposed"
    return "On Track"


# ---- Look-ahead: driven by the SINGLE source of truth (spec-plan-editor) ----
ACTIVITIES_REPO = "riscv-admin/spec-plan-editor"
ACTIVITIES_PATH = "web/activities.yaml"
# activities.yaml uses "Freezing"; Jira status uses "Specification in Freeze".
STATUS_TO_PHASE = {
    "Specification Inception": "Inception",
    "Specification in Planning": "Planning",
    "Specification Under Development": "Development",
    "Specification Under Stabilization": "Stabilization",
    "Specification in Freeze": "Freezing",
    "Specification in Ratification-Ready": "Ratification-Ready",
    "Specification in Publication": "Publication",
}
# Embedded fallback ONLY if the repo is unreachable during a build — keeps the
# build alive; the live values always come from activities.yaml.
_FALLBACK_ACTIVITIES = {
    "Inception": [["x", 30]],
    "Planning": [["x", 30], ["x", 14], ["x", 14]],
    "Development": [["x", 60], ["x", 14], ["x", 14]],
    "Stabilization": [["x", 14], ["x", 30], ["x", 14]],
    "Freezing": [["x", 45], ["x", 7], ["x", 15], ["x", 14]],
    "Ratification-Ready": [["x", 30], ["x", 15], ["x", 0], ["x", 14]],
    "Publication": [["x", 1]],
}


def load_phase_model(github_session):
    """Ingest activities.yaml from spec-plan-editor (single source of truth).

    Returns (phase_order, phase_dur, gate_dur). Falls back to an embedded
    snapshot only if the repo cannot be fetched, so a build never breaks.
    """
    acts = None
    data = _gh_get(github_session, f"/repos/{ACTIVITIES_REPO}/contents/{ACTIVITIES_PATH}")
    if isinstance(data, dict) and data.get("content"):
        try:
            acts = yaml.safe_load(base64.b64decode(data["content"]).decode())["activities"]
            print(f"activities.yaml: loaded from {ACTIVITIES_REPO}/{ACTIVITIES_PATH}")
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: could not parse activities.yaml ({exc}); using fallback")
    if acts is None:
        print("WARN: using embedded activities fallback (repo unreachable)")
        acts = _FALLBACK_ACTIVITIES
    phase_order = list(acts.keys())
    phase_dur = {p: sum(int(a[1]) for a in acts[p]) for p in phase_order}
    gate_dur = {p: (int(acts[p][-1][1]) if acts[p] else 14) for p in phase_order}
    return phase_order, phase_dur, gate_dur


def get_days_in_phase(jira, issue_key, full_status, created):
    """Days since the spec entered its current status (via changelog)."""
    entry = (created or "")[:10]
    try:
        cl = jira.get_issue_changelog(issue_key, start=0, limit=1000)
        for h in (cl.get("values") or cl.get("histories") or []):
            for it in h.get("items", []):
                if it.get("field") == "status" and it.get("toString") == full_status:
                    entry = h["created"][:10]
    except Exception:  # noqa: BLE001 - if changelog unavailable, fall back to created
        pass
    try:
        return (date.today() - date.fromisoformat(entry)).days
    except Exception:  # noqa: BLE001
        return None


def compute_lookahead(phase_model, phase, dip, awaiting):
    """Earliest plan-based ratification date from the current phase.

    Uses activities.yaml durations only. Remaining-in-phase = the phase's final
    gate duration if awaiting a vote (or over budget), else phase_dur - dip.
    Returns (runway_days, lookahead_date_str, reaches_year_bool) or (None, "", None).
    """
    phase_order, phase_dur, gate_dur = phase_model
    if phase not in phase_order:
        return None, "", None
    idx = phase_order.index(phase)
    floor = gate_dur[phase]
    rem_cur = floor if awaiting else max(phase_dur[phase] - (dip or 0), floor)
    downstream = sum(phase_dur[p] for p in phase_order[idx + 1:])
    runway = rem_cur + downstream
    la = date.fromordinal(date.today().toordinal() + runway)
    return runway, str(la), (la <= date(date.today().year, 12, 31))


def fetch_all_issues(jira, jql, page_size=100):
    issues = []
    fields_param = ",".join(REQUIRED_FIELDS)

    if jira.cloud:
        next_page_token = None
        while True:
            response = jira.enhanced_jql(
                jql=jql,
                limit=page_size,
                nextPageToken=next_page_token,
                fields=fields_param,
            )
            batch = response.get('issues', []) if response else []
            if not batch:
                break

            issues.extend(batch)
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break

        return issues

    start = 0
    while True:
        response = jira.jql(jql=jql, start=start, limit=page_size, fields=fields_param)
        batch = response.get('issues', []) if response else []
        if not batch:
            break

        issues.extend(batch)
        start += len(batch)
        total = response.get('total')
        if total is not None and start >= total:
            break

    return issues


# Function to parse and extract issue details
def parse_issues(issues, jira=None, github_session=None, phase_model=None):
    if github_session is None:
        github_session = make_github_session()
    if phase_model is None:
        phase_model = load_phase_model(github_session)

    parsed_issues = []
    contribution_cache = {}
    for issue in issues:
        issue_id = issue.get('id')
        issue_key = issue.get('key')
        fields = issue.get('fields', {})

        # Extracting various fields from the issue
        url = "https://riscv.atlassian.net/browse/" + issue_key
        summary = fields.get('summary')
        status = fields.get('status', {}).get('name')
        isa_or_non_isa = (
            fields.get('customfield_10042', {}).get('value') if fields.get('customfield_10042') else "Not Set Yet"
        )
        updated = fields.get('updated')

        # Safely accessing custom fields with a fallback to "Not Planned Yet" if None
        baseline_ratification_quarter = (
            fields.get('customfield_10039', {}).get('value') if fields.get('customfield_10039') else "Not Set Yet"
        )
        target_ratification_quarter = (
            fields.get('customfield_10040', {}).get('value') if fields.get('customfield_10040') else "Not Set Yet"
        )
        ratification_progress = (
            "Not Set Yet" if fields.get('customfield_10038') is None or fields.get('customfield_10038', {}).get('value') in [None, "Not Set"]
            else fields.get('customfield_10038', {}).get('value')
        )
        previous_ratification_progress = (
            "Not Set Yet" if fields.get('customfield_10136') is None or fields.get('customfield_10136', {}).get('value') in [None, "Not Set"]
            else fields.get('customfield_10136', {}).get('value')
        )
        github = (
            fields.get('customfield_10043', {}) if fields.get('customfield_10043') else "Not Set Yet"
        )
        bod_report = normalize_bod_report_value(fields.get('customfield_10037'))
        arc_review_status = extract_arc_review_status(fields.get('subtasks'))
        fast_track = "Yes" if is_fast_track(fields.get('subtasks')) else "No"

        # Resolve the last real code contribution from the spec's GitHub link.
        github_url = str(github).strip() if github and github != "Not Set Yet" else ""
        if github_url in contribution_cache:
            last_contribution, last_contribution_source = contribution_cache[github_url]
        elif github_url:
            last_contribution, last_contribution_source = get_last_contribution(
                github_url, github_session
            )
            contribution_cache[github_url] = (last_contribution, last_contribution_source)
        else:
            last_contribution, last_contribution_source = "", ""

        # Task completion % and computed Ratification Progress (dashboard view)
        done, total, pct_complete, work_open, gate_open = subtask_stats(fields.get('subtasks'))
        computed_progress = derive_ratification_progress(
            fields, work_open, gate_open, total, last_contribution
        )
        current_progress_raw = (
            fields.get('customfield_10038', {}).get('value') if fields.get('customfield_10038') else None
        )

        # Look-ahead from the current phase, using activities.yaml durations
        awaiting_vote = total > 0 and work_open == 0 and gate_open >= 1
        phase = STATUS_TO_PHASE.get(status)
        dip = get_days_in_phase(jira, issue_key, status, fields.get('created')) if jira else None
        runway_days, lookahead_date, reaches_year = compute_lookahead(
            phase_model, phase, dip, awaiting_vote
        )

        # Collect issue information
        parsed_issues.append({
            'URL': url,
            'ID': issue_id,
            'Key': issue_key,
            'Summary': summary,
            'Status': status,
            'BoD Report': bod_report,
            'ARC Review Status': arc_review_status,
            'Fast Track': fast_track,
            'Updated': updated,
            'GitHub': github,
            'ISA or NON-ISA': isa_or_non_isa,
            'Baseline Ratification Quarter': baseline_ratification_quarter,
            'Target Ratification Quarter': target_ratification_quarter,
            'Ratification Progress': ratification_progress,
            'Previous Ratification Progress': previous_ratification_progress,
            'Last Contribution': last_contribution,
            'Last Contribution Source': last_contribution_source,
            # computed fields
            'Tasks Done': done,
            'Tasks Total': total,
            '% Complete': pct_complete,
            'Computed Progress': computed_progress,
            'Current Progress Raw': current_progress_raw,
            'Days In Phase': dip if dip is not None else '',
            'Look-ahead Date': lookahead_date,
            'Reaches Year': ('Yes' if reaches_year else 'No') if reaches_year is not None else '',
        })
    return parsed_issues


def update_progress_in_jira(jira, parsed_issues):
    """Write the computed Ratification Progress back to Jira, idempotently.

    Only writes when the computed value differs from the current one, so daily
    runs do NOT churn the 'Updated' timestamp (staff automation must not look
    like developer activity). Never touches BoD Report (cf10037). 'Watch' is
    manual, so a manually-set value is only replaced when the computed status
    genuinely changes.
    """
    print("Updating Ratification Progress in Jira (only where changed)...")
    updated = 0
    for it in parsed_issues:
        computed = it.get('Computed Progress')
        current = it.get('Current Progress Raw')
        if not computed or computed == current:
            continue
        payload = {'customfield_10038': {'value': computed}}
        if current:
            payload['customfield_10136'] = {'value': current}  # preserve history
        try:
            jira.update_issue_field(it['Key'], payload)
            it['Previous Ratification Progress'] = current or it.get('Previous Ratification Progress')
            updated += 1
        except Exception as exc:  # noqa: BLE001 - log and continue
            print(f"  WARN: could not update {it['Key']}: {exc}")
    print(f"Ratification Progress updated on {updated} issue(s).")

def get_data_from_jira(jira_token, jira_email):
    """
    Fetch data from JIRA with the given JIRA_TOKEN and JQL (JIRA Query Language)
    and write the data to a CSV file.

    Parameters:
    jira_token (str): The JIRA token used for authentication.
    """
    print("Fetching data from JIRA...")
    jira = Jira(
        url="https://riscv.atlassian.net",
        username=jira_email,
        password=jira_token,
        cloud=True
    )

    # JQL query to fetch required issues
    jql = ('project = RVS AND '
           'issuetype not in subTaskIssueTypes() AND '
           'status NOT IN ("Specification Ratified", "Specification Not Ratified") '
           'ORDER BY priority DESC, updated DESC')

    # Extract issues from the JSON data
    issues = fetch_all_issues(jira, jql)

    # Single source of truth for phase durations: spec-plan-editor/activities.yaml
    github_session = make_github_session()
    phase_model = load_phase_model(github_session)

    parsed_issues = parse_issues(issues, jira=jira, github_session=github_session,
                                 phase_model=phase_model)

    # Write the computed Ratification Progress back to Jira (idempotent)
    update_progress_in_jira(jira, parsed_issues)

    # Generating the CSV filename with current date and time
    print("Generating csv file...")
    csv_filename = f"specs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    # Open (or create) a CSV file and write data to it
    with open(csv_filename, 'w', newline='') as file:
        writer = csv.writer(file, quotechar="'", quoting=csv.QUOTE_MINIMAL)
        # Writing the header row
        writer.writerow([
            'Jira URL',
            'Summary',
            'Status',
            'BoD Report',
            'ARC Review Status',
            'Fast Track',
            'Updated',
            'ISA or NON-ISA?',
            'GitHub',
            'Baseline Ratification Quarter',
            'Target Ratification Quarter',
            '% Complete',
            'Ratification Progress',
            'Previous Ratification Progress',
            'Days In Phase',
            'Look-ahead Date',
            'Reaches Year',
            'Last Contribution',
            'Last Contribution Source'
        ])

        # Writing each issue to the CSV file
        for issue in parsed_issues:
            writer.writerow([
                issue['URL'],
                issue['Summary'],
                issue['Status'],
                issue['BoD Report'],
                issue['ARC Review Status'],
                issue['Fast Track'],
                issue['Updated'],
                issue['ISA or NON-ISA'],
                issue['GitHub'],
                issue['Baseline Ratification Quarter'],
                issue['Target Ratification Quarter'],
                issue['% Complete'],
                # dashboard shows the computed value (source of truth for cf10038)
                issue['Computed Progress'],
                issue['Previous Ratification Progress'],
                issue['Days In Phase'],
                issue['Look-ahead Date'],
                issue['Reaches Year'],
                issue['Last Contribution'],
                issue['Last Contribution Source']
            ])

    print(f"Data successfully written to {csv_filename}")


def get_csv_content(csv_filepath):
    """
    Read a CSV file and return its content as a list of rows.

    Parameters:
    csv_filepath (str): The path to the CSV file.

    Returns:
    list: List of rows from the CSV file.
    """
    with open(csv_filepath, 'r') as csv_file:
        csv_reader = csv.reader(csv_file, quotechar="'")
        return list(csv_reader)


def read_csv_file(file_path):
    """
    Function to read a CSV file and return its content as a list of rows.

    Parameters:
    file_path (str): The path to the CSV file.

    Returns:
    list: List of rows from the CSV file.
    """
    with open(file_path, 'r') as csv_file:
        csv_reader = csv.reader(csv_file)
        return list(csv_reader)


def main():
    """
    The main function to run the whole script.
    """
    # Check for both JIRA_TOKEN and JIRA_EMAIL environment variables
    if not os.getenv('JIRA_EMAIL'):
        raise EnvironmentError("""
            Error: Required environment variable is not set.
            Please check that you have set the following environment variable:
            - JIRA_EMAIL
        """)

    if not os.getenv('JIRA_TOKEN'):
        raise EnvironmentError("""
            Error: Required environment variable is not set.
            Please check that you have set the following environment variable:
            - JIRA_TOKEN
        """)

    # Fetch data from JIRA and write to CSV
    get_data_from_jira(os.getenv('JIRA_TOKEN'), os.getenv('JIRA_EMAIL'))


if __name__ == '__main__':
    main()
