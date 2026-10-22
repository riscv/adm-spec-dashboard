"""Measure the activity of each spec over the last 90 days, from five sources.

Activity belongs to the spec, not to a group: a fast track has no developing
group, and its governing committee's activity says nothing about it. A spec
developed by a TG also gets the TG's own mailing list and Confluence space.

  J  Jira         approval subtasks closed, phase changes, comments
  G  GitHub       the spec's own GitHub link: commits (a fork's own, not
                  upstream syncs), PRs and PR comments
  M  Discussion   messages naming the spec's extensions on the ISA lists and
                  the governing committee's list (plus the TG's list)
  C  Confluence   edits to the Ratification Plan page and its children
                  (plus the TG's space)
  T  Tests        PRs naming the extensions in riscv-arch-test, sail-riscv
                  and riscv-unified-db

RVI staff and bots are left out of everything but Jira gates: a closed
approval subtask is progress whoever closes it. LFX meetings are a sixth
source to come; a source a spec does not have is left out of its score, so
adding one changes nothing else.

Each source scores 0, 1 or 2 (see score_*). The level is the share of the
points the spec could get: 80% Very Active, 50% Active, above 0 Low, else
Stalled.

Usage: python spec-activity.py CSV [--out web-ui/public/activity.json] [--only RVS-1,RVS-2]
Env:   JIRA_TOKEN, JIRA_EMAIL, GHTOKEN, GROUPSIO_USER, GROUPSIO_PASSWORD
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time

import requests

WINDOW_DAYS = 90
JIRA = "https://riscv.atlassian.net"
GITHUB_API = "https://api.github.com"
GROUPSIO_API = "https://groups.io/api/v1"
ISA_LISTS = ["tech-unprivileged", "tech-privileged", "tech-arch-review", "isa-dev"]
TEST_REPOS = ["riscv-non-isa/riscv-arch-test", "riscv/sail-riscv", "riscv/riscv-unified-db"]
EXTENSIONS_FIELD = "customfield_10044"
RATPLAN_FIELD = "customfield_10244"
GROUPSIO_FIELD = "customfield_10071"
CONFLUENCE_SPACE_FIELDS = ("customfield_10508", "customfield_10122")  # space key, space URL
# Tests are part of the work from Freeze on, so only then does having none count against a spec.
TESTS_EXPECTED = ("Specification in Freeze", "Specification in Ratification-Ready")
DONE = {"Done", "Closed", "Approved", "Resolved"}

# A word in the Extensions field counts as an extension name only in this shape,
# so prose ("will be defined as...") and one-letter names ("P") are not searched.
EXT_NAME = re.compile(r"^(?:Z[a-z][a-z0-9]+|S[mshvdu][a-z][a-z0-9]+|RV[ABM]\d\d(?:\.\d|P\d)?)$")

LEVELS = [(0.8, "Very Active"), (0.5, "Active")]


# ----- scoring (pure) -----

def score_jira(gates, phase_changes, comments):
    if gates >= 2 or phase_changes >= 1:
        return 2
    return 1 if gates or comments else 0


def score_github(commits, people):
    if commits is None:
        return None
    if people >= 3 or (commits >= 5 and people >= 2):
        return 2
    return 1 if commits or people else 0


def score_discussion(messages, people, tg_messages=None, tg_people=None):
    points = 2 if messages >= 5 and people >= 3 else 1 if messages else 0
    if tg_messages is not None:
        tg = 2 if tg_messages >= 10 and tg_people >= 5 else 1 if tg_messages >= 3 else 0
        points = max(points, tg)
    return points


def score_confluence(ratplan_edits, tg_pages):
    if ratplan_edits is None and tg_pages is None:
        return None
    if (ratplan_edits or 0) >= 3 or (tg_pages or 0) >= 6:
        return 2
    return 1 if ratplan_edits or tg_pages else 0


def score_tests(prs, people, status):
    if prs:
        return 2 if prs >= 3 and people >= 2 else 1
    return 0 if status in TESTS_EXPECTED else None


def level(points):
    scored = [p for p in points if p is not None]
    if not scored:
        return "Unknown", None
    share = sum(scored) / (2 * len(scored))
    for floor, name in LEVELS:
        if share >= floor:
            return name, round(share * 100)
    return ("Low" if share > 0 else "Stalled"), round(share * 100)


def extension_names(field_text, summary):
    """Search terms: extension names from the Extensions field, else from the
    summary, else the summary itself as a phrase.

    In a summary, names are only taken from the parentheses ("Name (Zext)") or
    when the whole summary is one name, since a title word like "Supervisor"
    has the same shape as an extension name.
    """
    summary = (summary or "").strip()
    in_parens = " ".join(re.findall(r"\(([^)]*)\)", summary))
    for text in (field_text or "", in_parens, summary if " " not in summary else ""):
        text = re.sub(r"\((?:previously|formerly)[^)]*\)", " ", text)
        words = [w.rstrip(".") for w in re.findall(r"[A-Za-z][A-Za-z0-9.]*", text)]
        names = list(dict.fromkeys(w for w in words if EXT_NAME.match(w)))
        if names:
            return names[:8]
    phrase = re.sub(r"\s*\(.*?\)", "", summary).strip()
    return [f'"{phrase}"'] if phrase else []


def adf_text(node):
    """Plain text of an Atlassian Document Format value (v3 textarea fields)."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    text = node.get("text", "") if node.get("type") == "text" else ""
    text += "".join(adf_text(c) for c in node.get("content", []))
    return text + (" " if node.get("type") in ("paragraph", "listItem", "hardBreak") else "")


def plural(n, word):
    if n == 1:
        return f"1 {word}"
    return f"{n} " + ("people" if word == "person" else word + "s")


# ----- staff -----

def load_staff(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        "names": {n.lower() for n in data["names"]},
        "github": {n.lower() for n in data["github"]},
        "groupsio": set(data["groupsio_user_ids"]),
    }


def is_bot(login):
    login = (login or "").lower()
    return login.endswith("[bot]") or login in {"github-actions", "dependabot", "web-flow"}


# ----- clients -----

class Sources:
    def __init__(self, staff, since):
        self.staff = staff
        self.since = since.isoformat()
        self.jira = requests.Session()
        self.jira.auth = (os.environ["JIRA_EMAIL"], os.environ["JIRA_TOKEN"])
        self.gh = requests.Session()
        self.gh.headers.update({"Accept": "application/vnd.github+json", "User-Agent": "riscv-adm-spec-dashboard"})
        if os.getenv("GHTOKEN"):
            self.gh.headers["Authorization"] = f"Bearer {os.environ['GHTOKEN']}"
        self.gio = None
        if os.getenv("GROUPSIO_USER"):
            self.gio = requests.Session()
            self.gio.post(f"{GROUPSIO_API}/login", timeout=30,
                          data={"email": os.environ["GROUPSIO_USER"], "password": os.environ["GROUPSIO_PASSWORD"]})
        self._issues = {}
        self._users = {}

    # --- helpers ---
    def _get(self, session, url, params=None, tries=5):
        for attempt in range(tries):
            try:
                resp = session.get(url, params=params, timeout=30)
            except requests.RequestException:
                resp = None
            if resp is not None and resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    pass
            if resp is not None and resp.status_code in (401, 404, 410, 422):
                return None
            time.sleep(3 * (attempt + 1))  # rate limited or a transient error
        return None

    def jira_issue(self, key, fields):
        if (key, fields) not in self._issues:
            data = self._get(self.jira, f"{JIRA}/rest/api/3/issue/{key}", {"fields": fields}) or {}
            self._issues[(key, fields)] = data.get("fields", {})
        return self._issues[(key, fields)]

    def gh_pages(self, path, params, limit=1000):
        out, page = [], 1
        while len(out) < limit:
            batch = self._get(self.gh, f"{GITHUB_API}/{path}", {**params, "per_page": 100, "page": page})
            if batch is None:
                return out if out else None
            out.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return out

    def is_staff_name(self, name):
        return (name or "").lower() in self.staff["names"]

    # --- J ---
    def jira_signal(self, key):
        resp = self.jira.post(f"{JIRA}/rest/api/3/search/jql", timeout=30, json={
            "jql": f'(key = {key} OR parent = {key}) AND updated >= "{self.since}"',
            "fields": ["status", "resolutiondate", "summary"], "maxResults": 100})
        issues = resp.json().get("issues", []) if resp.status_code == 200 else []
        gates = [i["fields"]["summary"] for i in issues
                 if i["key"] != key and i["fields"]["status"]["name"] in DONE
                 and (i["fields"].get("resolutiondate") or "")[:10] >= self.since]
        phases, comments = 0, 0
        for issue in issues:
            if issue["key"] == key:
                log = self._get(self.jira, f"{JIRA}/rest/api/3/issue/{key}/changelog", {"maxResults": 100}) or {}
                phases = sum(1 for h in log.get("values", []) if h["created"][:10] >= self.since
                             and any(item["field"] == "status" for item in h["items"]))
            got = self._get(self.jira, f"{JIRA}/rest/api/3/issue/{issue['key']}/comment", {"maxResults": 100}) or {}
            comments += sum(1 for c in got.get("comments", []) if c["created"][:10] >= self.since
                            and not self.is_staff_name(c["author"].get("displayName")))
        return {"gates": len(gates), "gate_names": [re.sub(r"^\[[^\]]*\]\s*-?\s*", "", g)[:60] for g in gates],
                "phase_changes": phases, "comments": comments}

    # --- G ---
    def github_signal(self, url):
        m = re.match(r"https://github.com/([^/]+)/([^/#?]+)(?:/(pull|tree|blob|commit)/([^#?]+))?", url or "")
        if not m:
            return None
        owner, name, kind, rest = m.groups()
        name = name.removesuffix(".git")
        if kind == "commit":
            return None  # one commit is not something that can show activity
        people, prs, where = set(), 0, f"{owner}/{name}"
        if kind == "pull":
            num = rest.split("/")[0]
            commits = self.gh_pages(f"repos/{owner}/{name}/pulls/{num}/commits", {})
            if commits is None:
                return None
            commits = [c for c in commits if c["commit"]["committer"]["date"][:10] >= self.since]
            since = {"since": self.since + "T00:00:00Z"}
            for path in (f"issues/{num}/comments", f"pulls/{num}/comments"):
                for c in self.gh_pages(f"repos/{owner}/{name}/{path}", since) or []:
                    if not is_bot(c["user"]["login"]) and c["user"]["login"].lower() not in self.staff["github"]:
                        people.add(c["user"]["login"].lower())
            where = f"{owner}/{name} PR #{num}"
        elif kind in ("tree", "blob"):
            branch, _, path = rest.partition("/")
            commits = self.gh_pages(f"repos/{owner}/{name}/commits",
                                    {"sha": branch, "path": path, "since": self.since + "T00:00:00Z"})
            if commits is None:
                return None
            where = f"{owner}/{name} {branch}/{path}".rstrip("/")
        else:
            info = self._get(self.gh, f"{GITHUB_API}/repos/{owner}/{name}")  # follows renames
            if not info:
                return None
            full = info["full_name"]
            where = full
            commits = self.gh_pages(f"repos/{full}/commits", {"since": self.since + "T00:00:00Z"}) or []
            if info.get("fork") and info.get("parent"):
                # A spec fork of riscv-isa-manual also gets upstream syncs; keep only its own commits.
                parent, branch = info["parent"]["full_name"], info["parent"]["default_branch"]
                own = []
                for c in commits[:300]:
                    cmp_ = self._get(self.gh, f"{GITHUB_API}/repos/{parent}/compare/{branch}...{c['sha']}")
                    if not (cmp_ and cmp_.get("status") in ("behind", "identical")):
                        own.append(c)
                commits = own
            pulls = self.gh_pages(f"repos/{full}/pulls", {"state": "all", "sort": "updated", "direction": "desc"},
                                  limit=300) or []
            pulls = [p for p in pulls if p["updated_at"][:10] >= self.since and not is_bot(p["user"]["login"])
                     and p["user"]["login"].lower() not in self.staff["github"]]
            prs = len(pulls)
            people |= {p["user"]["login"].lower() for p in pulls}
        kept = 0
        for c in commits:
            login = ((c.get("author") or {}).get("login") or "").lower()
            author = (c["commit"]["author"].get("name") or "").lower()
            if is_bot(login) or login in self.staff["github"] or author in self.staff["names"]:
                continue
            kept += 1
            people.add(login or author)
        return {"where": where, "commits": kept, "prs": prs, "people": len(people)}

    # --- M ---
    def groupsio_search(self, group, term):
        if not self.gio:
            return []
        data = self._get(self.gio, f"{GROUPSIO_API}/searcharchives", {
            "group_name": f"risc-v+{group}", "q": term, "limit": 100,
            "sort_field": "created", "sort_dir": "desc"}) or {}
        time.sleep(0.5)
        return data.get("data") or []

    def groupsio_messages(self, group):
        """Every message on a list in the window: (count, distinct posters), staff excluded."""
        if not self.gio:
            return None, None
        count, posters, token = 0, set(), None
        while True:
            params = {"group_name": f"risc-v+{group}", "limit": 100, "sort_dir": "desc"}
            if token:
                params["page_token"] = token
            data = self._get(self.gio, f"{GROUPSIO_API}/getmessages", params)
            if not data or "data" not in data:
                return None, None
            for msg in data["data"] or []:
                if msg["created"][:10] < self.since:
                    return count, len(posters)
                if self._staff_msg(msg):
                    continue
                count += 1
                posters.add(msg["user_id"])
            if not data.get("has_more"):
                return count, len(posters)
            token = data["next_page_token"]

    def _staff_msg(self, msg):
        return msg.get("user_id") in self.staff["groupsio"] or self.is_staff_name(msg.get("name"))

    def discussion_signal(self, terms, lists):
        seen, people, where = set(), set(), {}
        for group in dict.fromkeys(lists):
            for term in terms:
                for msg in self.groupsio_search(group, term):
                    if msg.get("created", "")[:10] < self.since or msg["id"] in seen or self._staff_msg(msg):
                        continue
                    seen.add(msg["id"])
                    people.add(msg.get("user_id"))
                    where[group] = where.get(group, 0) + 1
        return {"messages": len(seen), "people": len(people), "lists": where}

    # --- C ---
    def confluence_user(self, account_id):
        if account_id not in self._users:
            data = self._get(self.jira, f"{JIRA}/wiki/rest/api/user", {"accountId": account_id}) or {}
            self._users[account_id] = data.get("displayName") or ""
        return self._users[account_id]

    def ratplan_signal(self, url):
        m = re.search(r"/pages/(\d+)", url or "")
        if not m:
            return None
        ids = [m.group(1)]
        kids = self._get(self.jira, f"{JIRA}/wiki/api/v2/pages/{ids[0]}/descendants", {"limit": 250}) or {}
        ids += [d["id"] for d in kids.get("results", []) if d.get("type") == "page"]
        edits, editors = 0, set()
        for page_id in ids:
            versions = self._get(self.jira, f"{JIRA}/wiki/api/v2/pages/{page_id}/versions", {"limit": 250}) or {}
            for v in versions.get("results", []):
                if v["createdAt"][:10] >= self.since and not self.is_staff_name(self.confluence_user(v["authorId"])):
                    edits += 1
                    editors.add(v["authorId"])
        return {"page": ids[0], "edits": edits, "editors": len(editors)}

    def space_signal(self, space):
        """Pages in a group's space last edited by a member in the window."""
        pages, start = [], 0
        while True:
            data = self._get(self.jira, f"{JIRA}/wiki/rest/api/content/search", {
                "cql": f'space="{space}" and type in (page,blogpost) and lastmodified >= "{self.since}"',
                "expand": "history.lastUpdated", "limit": 100, "start": start})
            if data is None:
                return None
            pages.extend(data.get("results", []))
            if len(data.get("results", [])) < 100:
                break
            start += 100
        member = [p for p in pages if not self.is_staff_name(p["history"]["lastUpdated"]["by"].get("displayName"))]
        return {"pages": len(member), "editors": len({p["history"]["lastUpdated"]["by"].get("accountId") for p in member})}

    # --- T ---
    def tests_signal(self, terms):
        found, people = {}, set()
        for repo in TEST_REPOS:
            for term in terms:
                data = self._get(self.gh, f"{GITHUB_API}/search/issues", {
                    "q": f"repo:{repo} is:pr {term} updated:>={self.since}", "per_page": 50}) or {}
                time.sleep(2.2)  # the search API allows 30 requests a minute
                for item in data.get("items", []):
                    login = item["user"]["login"]
                    if is_bot(login) or login.lower() in self.staff["github"]:
                        continue
                    found[item["html_url"]] = repo.split("/")[1]
                    people.add(login.lower())
        repos = {}
        for repo in found.values():
            repos[repo] = repos.get(repo, 0) + 1
        return {"prs": len(found), "people": len(people), "repos": repos}

    # --- group context ---
    def list_slug(self, rvg_key):
        url = self.jira_issue(rvg_key, GROUPSIO_FIELD).get(GROUPSIO_FIELD) or ""
        m = re.search(r"/g/([^/?#]+)", url)
        return m.group(1) if m else None

    def space_key(self, rvg_key):
        fields = self.jira_issue(rvg_key, ",".join(CONFLUENCE_SPACE_FIELDS))
        if fields.get(CONFLUENCE_SPACE_FIELDS[0]) not in (None, "", "None"):
            return fields[CONFLUENCE_SPACE_FIELDS[0]]
        m = re.search(r"/spaces/([A-Za-z0-9]+)", fields.get(CONFLUENCE_SPACE_FIELDS[1]) or "")
        return m.group(1) if m else None

    def governing(self, key):
        for link in self.jira_issue(key, "issuelinks").get("issuelinks", []):
            other = link.get("inwardIssue")
            if link["type"]["name"] == "Governs" and other and other["key"].startswith("RVG-"):
                return other["key"], other["fields"]["summary"]
        return None, None


# ----- one spec -----

def explain(src):
    """One plain sentence per source, from the counts only."""
    lines = {}
    j = src["jira"]
    parts = []
    if j["gates"]:
        parts.append(f"{plural(j['gates'], 'approval step')} closed ({'; '.join(j['gate_names'][:3])}"
                     + ("; ..." if len(j["gate_names"]) > 3 else "") + ")")
    if j["phase_changes"]:
        parts.append(f"phase changed {plural(j['phase_changes'], 'time')}")
    if j["comments"]:
        parts.append(plural(j["comments"], "comment") + " from members")
    lines["jira"] = "; ".join(parts) or "no approval step closed, no phase change, no member comment"
    g = src.get("github")
    if g:
        bits = [plural(g["commits"], "commit")]
        if g["prs"]:
            bits.append(plural(g["prs"], "PR"))
        lines["github"] = f"{', '.join(bits)} by {plural(g['people'], 'person')} in {g['where']}"
    m = src["discussion"]
    text = f"{plural(m['messages'], 'message')} from {plural(m['people'], 'person')} naming {', '.join(src['terms'])}"
    if m["lists"]:
        text += " (" + ", ".join(f"{k} {v}" for k, v in sorted(m["lists"].items())) + ")"
    if src.get("tg_list"):
        tl = src["tg_list"]
        text += f"; the group's list had {plural(tl['messages'], 'message')} from {plural(tl['people'], 'person')}"
    lines["discussion"] = text
    c = []
    if src.get("ratplan") is not None:
        c.append(f"Ratification Plan: {plural(src['ratplan']['edits'], 'edit')} by {plural(src['ratplan']['editors'], 'member')}")
    if src.get("space") is not None:
        c.append(f"group space: {plural(src['space']['pages'], 'page')} edited by members")
    if c:
        lines["confluence"] = "; ".join(c)
    t = src.get("tests")
    if t and (t["prs"] or src["tests_expected"]):
        lines["tests"] = (f"{plural(t['prs'], 'PR')} by {plural(t['people'], 'person')} ("
                          + ", ".join(f"{k} {v}" for k, v in sorted(t["repos"].items())) + ")") if t["prs"] \
            else "no PRs in riscv-arch-test, sail-riscv or riscv-unified-db"
    return lines


def measure(sources, row):
    key = row["Jira URL"].rstrip("/").rsplit("/", 1)[-1]
    status = row.get("Status", "")
    fields = sources.jira_issue(key, f"{EXTENSIONS_FIELD},{RATPLAN_FIELD}")
    terms = extension_names(" ".join(adf_text(fields.get(EXTENSIONS_FIELD)).split()), row.get("Summary"))
    gov_key, gov_name = sources.governing(key)
    gov_list = sources.list_slug(gov_key) if gov_key else None
    tg_key = row.get("Group Key") or ""
    src = {"terms": terms, "tests_expected": status in TESTS_EXPECTED}
    src["jira"] = sources.jira_signal(key)
    src["github"] = sources.github_signal(row.get("GitHub"))
    src["discussion"] = sources.discussion_signal(terms, ISA_LISTS + ([gov_list] if gov_list else []))
    src["ratplan"] = sources.ratplan_signal(fields.get(RATPLAN_FIELD))
    code_terms = [t for t in terms if not t.startswith('"')] or terms
    src["tests"] = sources.tests_signal(code_terms)
    if tg_key:
        slug, space = sources.list_slug(tg_key), sources.space_key(tg_key)
        if slug:
            n, p = sources.groupsio_messages(slug)
            if n is not None:
                src["tg_list"] = {"messages": n, "people": p}
        if space:
            src["space"] = sources.space_signal(space)

    j, g, m, t = src["jira"], src["github"], src["discussion"], src["tests"]
    tl = src.get("tg_list") or {}
    points = {
        "jira": score_jira(j["gates"], j["phase_changes"], j["comments"]),
        "github": score_github(g["commits"], g["people"]) if g else None,
        "discussion": score_discussion(m["messages"], m["people"], tl.get("messages"), tl.get("people")),
        "confluence": score_confluence((src["ratplan"] or {}).get("edits"), (src.get("space") or {}).get("pages")),
        "tests": score_tests(t["prs"], t["people"], status),
        "meetings": None,  # LFX, to come
    }
    name, share = level(points.values())
    return key, {
        "level": name, "share": share, "points": points, "text": explain(src),
        "governing": gov_name or "", "terms": terms,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv")
    ap.add_argument("--out", default="web-ui/public/activity.json")
    ap.add_argument("--staff", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "staff.json"))
    ap.add_argument("--only", default="")
    args = ap.parse_args(argv)

    today = dt.date.today()
    since = today - dt.timedelta(days=WINDOW_DAYS)
    sources = Sources(load_staff(args.staff), since)
    if not sources.gio:
        print("GROUPSIO_USER not set: discussion counts will be 0", file=sys.stderr)
    only = {k.strip() for k in args.only.split(",") if k.strip()}
    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f, quotechar="'") if r.get("Status") != "Cancelled"]

    specs, failed = {}, []
    for row in rows:
        key = row["Jira URL"].rstrip("/").rsplit("/", 1)[-1]
        if only and key not in only:
            continue
        try:
            key, entry = measure(sources, row)
            specs[key] = entry
            print(f"{key} {entry['level']} {entry['points']}", file=sys.stderr, flush=True)
        except Exception as exc:  # one spec failing must not lose the others
            failed.append(key)
            print(f"{key} failed: {exc!r}", file=sys.stderr, flush=True)

    out = {"generated": today.isoformat(), "since": since.isoformat(), "window_days": WINDOW_DAYS, "specs": specs}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    print(f"{len(specs)} specs measured, {len(failed)} failed{': ' + ', '.join(failed) if failed else ''}",
          file=sys.stderr)
    return 1 if rows and not specs else 0


if __name__ == "__main__":
    sys.exit(main())
