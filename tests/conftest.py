"""Shared fixtures for the ai-summaries.py tests.

ai-summaries.py has a hyphen in its name, so it is loaded with importlib
instead of a normal import. No test talks to Bedrock: main() gets a fake
boto3 module and a fake client.
"""
import csv
import datetime as dt
import importlib.util
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TODAY = dt.date(2026, 10, 19)

# The columns get-specs-data.py writes, in the same order.
COLUMNS = [
    "Jira URL", "Summary", "Status", "BoD Report", "ARC Review Status", "Public Review Status",
    "TSC Approval Status", "BoD Approval Status", "Phase Tasks Status", "Fast Track", "Updated",
    "ISA or NON-ISA?", "GitHub", "Baseline Ratification Quarter", "Target Ratification Quarter",
    "Ratification Progress", "Previous Ratification Progress", "Last Contribution",
    "Last Contribution Source", "Plan Ratification Quarter", "Previous Target Ratification Quarter",
    "Target Changed", "Phase Started", "Development Started", "Next Approval", "Next Approval Date",
    "Latest Replanning", "Phase History", "Projected Phase Ends",
    "Group Key", "Group", "Group Type", "Group Activity Level", "Group Activity Signals", "Category",
]


def load_module():
    spec = importlib.util.spec_from_file_location("ai_summaries", ROOT / "ai-summaries.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ais():
    """A fresh copy of ai-summaries.py, so monkeypatched globals never leak."""
    return load_module()


def make_row(key="RVS-1", **fields):
    """A CSV row with every column present; unset columns are empty."""
    row = {c: "" for c in COLUMNS}
    row["Jira URL"] = f"https://riscv.atlassian.net/browse/{key}"
    row["Summary"] = f"Spec {key}"
    for name, value in fields.items():
        col = name.replace("_", " ")
        assert col in row, col
        row[col] = value
    return row


# Fixture rows, one per situation the dashboard shows.
ROWS = {
    "planning_empty": dict(
        Status="Specification in Planning", **{
            "Fast Track": "No",
            "Baseline Ratification Quarter": "Not Set Yet",
            "Target Ratification Quarter": "Not Set Yet",
            "Plan Ratification Quarter": "Not Set Yet",
            "Ratification Progress": "Not Set Yet",
        }),
    "development_on_track": dict(
        Status="Specification Under Development", **{
            "Fast Track": "No",
            "Phase Started": "2026-03-09",
            "Development Started": "2026-03-09",
            "Baseline Ratification Quarter": "27Q1",
            "Plan Ratification Quarter": "27Q1",
            "Target Ratification Quarter": "27Q1",
            "Ratification Progress": "On Track",
            "Previous Target Ratification Quarter": "26Q4",
            "Target Changed": "2026-03-09",
            "Next Approval": "Development complete",
            "Next Approval Date": "2026-12-25",
            "Latest Replanning": "2026-03-09T10:00:00Z",
            "Last Contribution": "2026-10-16T12:38:07Z",
        }),
    "stabilization_exposed_slip": dict(
        Status="Specification Under Stabilization", **{
            "Fast Track": "No",
            "Phase Started": "2026-06-22",
            "Development Started": "2025-01-10",
            "Baseline Ratification Quarter": "26Q4",
            "Plan Ratification Quarter": "Not Set Yet",
            "Target Ratification Quarter": "27Q2",
            "Ratification Progress": "Exposed",
            "Previous Target Ratification Quarter": "27Q1",
            "Target Changed": "2026-10-01",
            "Next Approval": "Stabilization approval",
            "Next Approval Date": "2027-02-01",
            "ARC Review Status": "Approval In Progress",
        }),
    "ratification_ready_late": dict(
        Status="Specification in Ratification-Ready", **{
            "Fast Track": "No",
            "Baseline Ratification Quarter": "25Q4",
            "Plan Ratification Quarter": "27Q1",
            "Target Ratification Quarter": "26Q4",
            "Ratification Progress": "Late",
            "Previous Target Ratification Quarter": "27Q1",
            "Target Changed": "2026-09-01",
            "ARC Review Status": "Approved",
            "Public Review Status": "Public Review Done",
            "TSC Approval Status": "Not Started",
            "BoD Approval Status": "Not Set",
        }),
    "cancelled": dict(
        Status="Cancelled", **{
            "Fast Track": "Yes",
            "Phase Started": "2025-01-01",
            "Target Ratification Quarter": "26Q2",
            "Ratification Progress": "Late",
            "Last Contribution": "2025-05-05T00:00:00Z",
        }),
    "fast_track_freeze": dict(
        Status="Specification in Freeze", **{
            "Fast Track": "Yes",
            "Phase Started": "2026-07-08",
            "Development Started": "2024-03-08",
            "Baseline Ratification Quarter": "Not Set Yet",
            "Plan Ratification Quarter": "Not Set Yet",
            "Target Ratification Quarter": "27Q1",
            "Ratification Progress": "On Track",
            "Next Approval": "Freeze approval",
            "Next Approval Date": "2026-12-19",
        }),
    "inception": dict(
        Status="Specification Inception", **{
            "Fast Track": "No",
            "Plan Ratification Quarter": "Not Set Yet",
            "Target Ratification Quarter": "27Q2",
            "Ratification Progress": "Not Set Yet",
        }),
}


@pytest.fixture
def rows():
    return {name: make_row(f"RVS-{i + 1}", **fields) for i, (name, fields) in enumerate(ROWS.items())}


def write_csv(path, rows):
    """Write rows the way get-specs-data.py does (quotechar ')."""
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, quotechar="'", quoting=csv.QUOTE_MINIMAL)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([r[c] for c in COLUMNS])


class FakeClient:
    """Stands in for the bedrock-runtime client.

    By default it answers with the facts joined as sentences (a faithful
    rewrite), wrapped in <think> tags the way Qwen3 sometimes answers.
    """

    def __init__(self, reply=None, error=None):
        self.calls = []
        self.reply = reply
        self.error = error

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        prompt = kwargs["messages"][0]["content"][0]["text"]
        facts = prompt.split("Facts:\n- ", 1)[1].split("\n- ")
        text = self.reply(facts) if self.reply else "<think>thinking</think> " + ". ".join(facts) + "."
        return {"output": {"message": {"content": [{"text": text}]}}}


@pytest.fixture
def run_main(ais, monkeypatch, tmp_path):
    """Run ai-summaries.main() on rows with a fake client and a fixed today.

    Returns (output dict or None, FakeClient). The output file persists
    between calls within one test, so a second call sees the first's cache.
    """
    csv_path = tmp_path / "latest.csv"
    out_path = tmp_path / "public" / "summaries.json"

    class FixedDate(dt.date):
        @classmethod
        def today(cls):
            return cls(TODAY.year, TODAY.month, TODAY.day)

    monkeypatch.setattr(ais, "dt", types.SimpleNamespace(date=FixedDate))

    def run(rows, client=None, extra=()):
        client = client or FakeClient()
        fake_boto3 = types.ModuleType("boto3")

        class Session:
            def __init__(self, region_name=None):
                self.region_name = region_name

            def client(self, name):
                assert name == "bedrock-runtime"
                return client

        fake_boto3.Session = Session
        monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
        write_csv(csv_path, rows)
        monkeypatch.setattr(sys, "argv", ["ai-summaries.py", str(csv_path), "--out", str(out_path), *extra])
        ais.main()
        import json
        out = json.loads(out_path.read_text()) if out_path.exists() else None
        return out, client

    run.out_path = out_path
    return run
