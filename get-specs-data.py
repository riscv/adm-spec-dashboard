"""
This script fetches data from a JIRA project and writes it to a CSV file.
The JIRA issues are retrieved using a JIRA Query Language (JQL) query,
and the data is written to a CSV file named with the current date and time.

Author: Rafael Sene, rafael@riscv.org - Initial implementation
"""

import csv
import os
from datetime import datetime

import requests
from atlassian import Jira

# Cache resolved latest-release PDFs per owner/repo so we hit the GitHub API
# at most once per repository during a single run.
_release_pdf_cache = {}


def get_latest_release_pdf(github_url):
    """Return the direct download URL of the latest GitHub release's PDF asset.

    Given a spec's GitHub repository URL, query the GitHub API for the latest
    release and return the ``browser_download_url`` of the first ``.pdf`` asset.
    Returns an empty string when there is no repo, no release, no PDF asset, or
    on any API/network error. Uses ``GITHUB_TOKEN`` when available to lift the
    unauthenticated rate limit (60/hr -> 5000/hr).
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
        )
        if response.status_code == 200:
            assets = response.json().get("assets", []) or []
            for asset in assets:
                name = (asset.get("name") or "").lower()
                if name.endswith(".pdf"):
                    pdf_url = asset.get("browser_download_url", "") or ""
                    break
        else:
            print(f"  No latest-release PDF for {cache_key} (HTTP {response.status_code})")
    except Exception as exc:  # network errors, JSON decode, etc.
        print(f"  Failed to resolve release PDF for {cache_key}: {exc}")

    _release_pdf_cache[cache_key] = pdf_url
    return pdf_url


def extract_field_value(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        return value.get('value') or value.get('name') or value.get('label') or ""
    if isinstance(value, list):
        parts = [extract_field_value(item) for item in value]
        return ", ".join([part for part in parts if part])
    return str(value)


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

        # Collect issue information
        parsed_issues.append({
            'URL': url,
            'ID': issue_id,
            'Key': issue_key,
            'Summary': summary,
            'Status': status,
            'BoD Report': bod_report,
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
           'issuetype not in subTaskIssueTypes() '
           'ORDER BY priority DESC, updated DESC')

    # Extract issues from the JSON data
    all_issues = jira.jql(jql)
    issues = all_issues.get('issues', [])
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
            if issue['Status'] != "Specification Ratified" and issue['Status'] != "Specification Not Ratified":
                latest_release_pdf = get_latest_release_pdf(
                    issue['GitHub'] if isinstance(issue['GitHub'], str) else ""
                )
                writer.writerow([
                    issue['URL'],
                    issue['Summary'],
                    issue['Status'],
                    issue['BoD Report'],
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
