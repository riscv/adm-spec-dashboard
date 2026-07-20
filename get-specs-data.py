"""
This script fetches data from a JIRA project and writes it to a CSV file.
The JIRA issues are retrieved using a JIRA Query Language (JQL) query,
and the data is written to a CSV file named with the current date and time.

Author: Rafael Sene, rafael@riscv.org - Initial implementation
"""

import csv
import os
import re
from datetime import datetime
from urllib.parse import urlparse

import requests
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
    "subtasks",
    "customfield_10037",
    "customfield_10038",
    "customfield_10039",
    "customfield_10040",
    "customfield_10042",
    "customfield_10043",
    "customfield_10136",
]


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
def parse_issues(issues, github_session=None):
    if github_session is None:
        github_session = make_github_session()

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
            'Last Contribution Source': last_contribution_source
        })
    return parsed_issues

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
    parsed_issues = parse_issues(issues)

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
            'Ratification Progress',
            'Previous Ratification Progress',
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
                issue['Ratification Progress'],
                issue['Previous Ratification Progress'],
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
