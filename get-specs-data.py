"""
This script fetches data from a JIRA project and writes it to a CSV file.
The JIRA issues are retrieved using a JIRA Query Language (JQL) query,
and the data is written to a CSV file named with the current date and time.

Author: Rafael Sene, rafael@riscv.org - Initial implementation
"""

import csv
import io
import os
import re
import zipfile
from datetime import datetime

import requests
from atlassian import Jira

# Cache resolved latest-release PDFs per owner/repo so we hit the GitHub API
# at most once per repository during a single run.
_release_pdf_cache = {}

# The shared ISA-manual monorepo (and its forks) publishes releases of the
# *entire* manual, never a single extension. A dashboard row that points at it
# (via /pull/, /blob/, /tree/, ...) must not surface a PDF, since that PDF is
# the full manual rather than the row's extension.
_MONOREPO_NAMES = {"riscv-isa-manual"}

# Generic full-volume asset names attached by the shared release CI. These are
# the complete privileged/unprivileged/unified manuals, not an extension doc,
# so they must never be surfaced as a spec's "latest release PDF".
_GENERIC_PDF_NAMES = {
    "riscv-privileged.pdf",
    "riscv-unprivileged.pdf",
    "riscv-isa.pdf",
}

# Specs still under development often live only as a pull request against the
# shared riscv-isa-manual monorepo — they have no dedicated repo or release. The
# manual's PR build does produce a PDF (the full manual with the PR's changes
# integrated), but only as an auth-gated, zipped, 7-day GitHub Actions artifact
# that an anonymous dashboard visitor cannot open. For those specs we download
# that artifact (the pipeline is authenticated) and re-host it as a stable,
# public asset on this repo's own "spec-pdfs" release, then link that URL.
_ISA_MANUAL_PR_PATTERN = re.compile(
    r"github\.com/([^/]+)/riscv-isa-manual/pull/(\d+)",
    re.IGNORECASE,
)

# Where re-hosted PR PDFs are written locally (picked up by the workflow's
# publish step) and the public base URL they are served from once published.
PR_PDF_CACHE_DIR = "pdf-cache"
PR_PDF_RELEASE_TAG = "spec-pdfs"
PR_PDF_PUBLIC_BASE = (
    "https://github.com/riscv/adm-spec-dashboard/releases/download/" + PR_PDF_RELEASE_TAG
)

# Per-run caches so each PR / the assets listing is resolved at most once.
_pr_pdf_cache = {}
_existing_pr_assets = None


def _github_headers():
    """Standard GitHub API headers, authenticated when GITHUB_TOKEN is set."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _get_existing_pr_pdf_assets():
    """Return the set of PDF asset names already on the stable spec-pdfs release.

    Lets a link survive the upstream artifact's 7-day expiry: if we cannot
    re-download a PR build this run but published a copy previously, the public
    URL still resolves. Cached for the run; returns an empty set on any error.
    """
    global _existing_pr_assets
    if _existing_pr_assets is not None:
        return _existing_pr_assets

    _existing_pr_assets = set()
    try:
        resp = requests.get(
            "https://api.github.com/repos/riscv/adm-spec-dashboard/releases/tags/"
            + PR_PDF_RELEASE_TAG,
            headers=_github_headers(),
            timeout=15,
        )
        if resp.status_code == 200:
            for asset in resp.json().get("assets", []) or []:
                name = asset.get("name")
                if name:
                    _existing_pr_assets.add(name)
    except Exception as exc:
        print(f"  Could not list existing {PR_PDF_RELEASE_TAG} assets: {exc}")
    return _existing_pr_assets


def get_latest_release_pdf(github_url):
    """Return the direct download URL of a spec's extension-specific release PDF.

    Given a spec's GitHub URL, query the GitHub API for the repository's latest
    release and return the ``browser_download_url`` of an *extension-specific*
    ``.pdf`` asset. Returns an empty string (so the dashboard shows no link)
    when the target is the shared ``riscv-isa-manual`` monorepo, when the
    release only carries the generic full-manual volumes
    (``riscv-privileged.pdf`` / ``riscv-unprivileged.pdf``), or when there is no
    repo, no release, no PDF asset, or any API/network error. A wrong link (the
    full privileged/unprivileged manual) is worse than none.

    Uses ``GITHUB_TOKEN`` when available to lift the unauthenticated rate limit
    (60/hr -> 5000/hr) and follows redirects so renamed repos still resolve.
    """
    if not github_url or not isinstance(github_url, str):
        return ""

    url = github_url.strip()
    if not url.lower().startswith("http") or "github.com" not in url.lower():
        return ""

    try:
        path = url.split("github.com/", 1)[1]
    except IndexError:
        return ""

    parts = [segment for segment in path.split("/") if segment]
    if len(parts) < 2:
        return ""

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    # The shared ISA-manual monorepo releases the whole manual, not a single
    # extension — never surface a PDF for rows that point at it.
    if repo.lower() in _MONOREPO_NAMES:
        return ""

    cache_key = f"{owner}/{repo}"
    if cache_key in _release_pdf_cache:
        return _release_pdf_cache[cache_key]

    pdf_url = ""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        response = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
            headers=headers,
            timeout=15,
            allow_redirects=True,
        )
        if response.status_code == 200:
            assets = response.json().get("assets", []) or []
            for asset in assets:
                name = (asset.get("name") or "").lower()
                if not name.endswith(".pdf"):
                    continue
                # Skip the generic full-manual volumes — these are attached by
                # the shared release CI to many extension repos and are not the
                # extension's own document.
                if name in _GENERIC_PDF_NAMES:
                    continue
                pdf_url = asset.get("browser_download_url", "") or ""
                break
        else:
            print(f"  No latest-release PDF for {cache_key} (HTTP {response.status_code})")
    except Exception as exc:  # network errors, JSON decode, etc.
        print(f"  Failed to resolve release PDF for {cache_key}: {exc}")

    _release_pdf_cache[cache_key] = pdf_url
    return pdf_url


def get_isa_manual_pr_pdf(github_url):
    """Re-host and return the public URL of a riscv-isa-manual PR build's PDF.

    When a spec's GitHub field points at a ``riscv-isa-manual`` pull request,
    find that PR's latest successful ISA-manual build, download its
    ``riscv-spec*.pdf`` artifact (the full manual with the PR integrated) into
    :data:`PR_PDF_CACHE_DIR`, and return the deterministic public URL under this
    repo's stable ``spec-pdfs`` release. The workflow uploads the cached files
    to that release after this script runs. Falls back to an already-published
    copy so links survive the upstream 7-day artifact expiry, and returns ""
    when nothing is available. Never raises.
    """
    if not github_url or not isinstance(github_url, str):
        return ""

    match = _ISA_MANUAL_PR_PATTERN.search(github_url)
    if not match:
        return ""

    owner, pr_number = match.group(1), match.group(2)
    cache_key = f"{owner}/riscv-isa-manual/pull/{pr_number}"
    if cache_key in _pr_pdf_cache:
        return _pr_pdf_cache[cache_key]

    asset_name = f"riscv-isa-manual-pr-{pr_number}.pdf"
    public_url = f"{PR_PDF_PUBLIC_BASE}/{asset_name}"
    headers = _github_headers()
    api = f"https://api.github.com/repos/{owner}/riscv-isa-manual"
    result = ""

    try:
        # 1. Resolve the PR's head commit.
        pr_resp = requests.get(f"{api}/pulls/{pr_number}", headers=headers, timeout=15)
        head_sha = (
            (pr_resp.json().get("head") or {}).get("sha")
            if pr_resp.status_code == 200
            else None
        )

        # 2. Find the newest successful ISA Build run for that commit and its
        #    (unexpired) riscv-spec*.pdf artifact.
        artifact = None
        if head_sha:
            runs_resp = requests.get(
                f"{api}/actions/runs",
                headers=headers,
                params={"head_sha": head_sha, "per_page": 30},
                timeout=15,
            )
            build_runs = []
            if runs_resp.status_code == 200:
                for run in runs_resp.json().get("workflow_runs", []) or []:
                    if "isa build" in (run.get("name") or "").lower() and (
                        run.get("conclusion") == "success"
                    ):
                        build_runs.append(run)
            build_runs.sort(key=lambda r: r.get("run_number", 0), reverse=True)

            for run in build_runs:
                arts_resp = requests.get(
                    f"{api}/actions/runs/{run['id']}/artifacts",
                    headers=headers,
                    timeout=15,
                )
                if arts_resp.status_code != 200:
                    continue
                for asset in arts_resp.json().get("artifacts", []) or []:
                    name = (asset.get("name") or "").lower()
                    if (
                        name.endswith(".pdf")
                        and "riscv-spec" in name
                        and not asset.get("expired", False)
                    ):
                        artifact = asset
                        break
                if artifact:
                    break

        # 3. Download the artifact zip and extract the PDF into the cache dir.
        if artifact:
            dl_resp = requests.get(
                artifact["archive_download_url"], headers=headers, timeout=120
            )
            if dl_resp.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(dl_resp.content)) as archive:
                    pdf_member = next(
                        (n for n in archive.namelist() if n.lower().endswith(".pdf")),
                        None,
                    )
                    if pdf_member:
                        os.makedirs(PR_PDF_CACHE_DIR, exist_ok=True)
                        out_path = os.path.join(PR_PDF_CACHE_DIR, asset_name)
                        with archive.open(pdf_member) as src, open(out_path, "wb") as dst:
                            dst.write(src.read())
                        result = public_url
                        print(f"  Cached PR PDF for {cache_key} -> {asset_name}")
    except Exception as exc:  # network errors, bad zip, etc.
        print(f"  Failed to resolve PR PDF for {cache_key}: {exc}")

    # Fall back to a previously published copy so the link survives expiry.
    if not result and asset_name in _get_existing_pr_pdf_assets():
        result = public_url

    _pr_pdf_cache[cache_key] = result
    return result


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
def parse_issues(issues):
    parsed_issues = []
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
            'Previous Ratification Progress': previous_ratification_progress
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
            'Latest Release PDF'
        ])

        # Writing each issue to the CSV file
        for issue in parsed_issues:
            github_url = issue['GitHub'] if isinstance(issue['GitHub'], str) else ""
            # Prefer a dedicated repo's release PDF; otherwise, for specs that
            # live only as a riscv-isa-manual PR, re-host that PR build's PDF.
            latest_release_pdf = (
                get_latest_release_pdf(github_url)
                or get_isa_manual_pr_pdf(github_url)
            )
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
                latest_release_pdf
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
