"""spec-activity.py: the scoring rules and the extension names it searches for.

Only the pure functions are tested; nothing here talks to Jira, GitHub,
Groups.io or Confluence.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("spec_activity", ROOT / "spec-activity.py")
sa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sa)


@pytest.mark.parametrize("gates, phases, comments, points", [
    (0, 0, 0, 0), (1, 0, 0, 1), (0, 0, 1, 1), (2, 0, 0, 2), (0, 1, 0, 2),
])
def test_jira(gates, phases, comments, points):
    assert sa.score_jira(gates, phases, comments) == points


@pytest.mark.parametrize("commits, people, points", [
    (None, 0, None), (0, 0, 0), (1, 1, 1), (0, 1, 1), (5, 2, 2), (4, 2, 1), (0, 3, 2),
])
def test_github(commits, people, points):
    assert sa.score_github(commits, people) == points


def test_discussion_takes_the_better_of_spec_threads_and_the_group_list():
    assert sa.score_discussion(0, 0) == 0
    assert sa.score_discussion(4, 4) == 1
    assert sa.score_discussion(5, 3) == 2
    assert sa.score_discussion(1, 1, tg_messages=10, tg_people=5) == 2
    assert sa.score_discussion(0, 0, tg_messages=2, tg_people=2) == 0


def test_confluence_is_left_out_without_a_plan_or_a_space():
    assert sa.score_confluence(None, None) is None
    assert sa.score_confluence(0, None) == 0
    assert sa.score_confluence(3, None) == 2
    assert sa.score_confluence(None, 6) == 2
    assert sa.score_confluence(1, 0) == 1


def test_tests_count_against_a_spec_only_from_freeze_on():
    assert sa.score_tests(0, 0, "Specification Under Development") is None
    assert sa.score_tests(0, 0, "Specification in Freeze") == 0
    assert sa.score_tests(1, 1, "Specification Under Development") == 1
    assert sa.score_tests(3, 2, "Specification in Planning") == 2


def pts(jira=None, github=None, discussion=None, confluence=None, tests=None):
    return {"jira": jira, "github": github, "discussion": discussion,
            "confluence": confluence, "tests": tests, "meetings": None}


@pytest.mark.parametrize("points, name, share", [
    (pts(2, 2, 2, 1, 2), "Very Active", 93),
    (pts(2, 1, 2, 0, 2), "Active", 71),
    (pts(2, 0, 1, 0, 1), "Low", 36),
    (pts(0, 0, 0), "Stalled", 0),
    (pts(), "Unknown", None),
])
def test_level(points, name, share):
    assert sa.level(points) == (name, share)


def test_github_and_discussion_count_double():
    # A spec in Freeze busy on GitHub and the lists, quiet in Jira, Confluence and tests
    assert sa.level(pts(0, 2, 2, 0, 0)) == ("Active", 57)


def test_a_missing_source_does_not_count_against_the_spec():
    # LFX meetings and a spec with no GitHub link
    assert sa.level(pts(jira=2, discussion=2)) == ("Very Active", 100)


@pytest.mark.parametrize("field, summary, names", [
    ("Smwg, Smwgd, Sswg", "RISC-V Worlds (Smwid, Smlwid)", ["Smwg", "Smwgd", "Sswg"]),
    ("Zvdot4a8i (previously known as Zvqdot)", "Dot-Product (Zvdot4a8i)", ["Zvdot4a8i"]),
    ("", "Vector Unzip Instruction (Zvzip)", ["Zvzip"]),
    ("RVA23.1", "RVA23.1", ["RVA23.1"]),
    ("P", "Packed SIMD (P)", ['"Packed SIMD"']),
    ("Instrumentation Trace will be defined as an extension", "Instrumentation Trace", ['"Instrumentation Trace"']),
    ("", "Supervisor Domains Access Protection (Smmtt)", ["Smmtt"]),
    ("", "Input/Output Physical Memory Protection (IOPMP)", ["IOPMP", '"Input/Output Physical Memory Protection"']),
    ("", "Confidential VM Extension (CoVE) with I/O", ["CoVE", '"Confidential VM Extension with I/O"']),
    ("", "Platform Management Interface - RPMI (2.0)", ["RPMI", '"Platform Management Interface"']),
    ("", "Implicit Return (E-Trace 2.0)", ['"Implicit Return"']),
    ("", "psABI", ['"psABI"']),
])
def test_extension_names(field, summary, names):
    assert sa.extension_names(field, summary) == names


def test_adf_text():
    doc = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Smtw, Shtw"}]}]}
    assert " ".join(sa.adf_text(doc).split()) == "Smtw, Shtw"


def test_plural():
    assert sa.plural(1, "person") == "1 person"
    assert sa.plural(3, "person") == "3 people"
    assert sa.plural(0, "commit") == "0 commits"


def test_staff_file_loads():
    staff = sa.load_staff(ROOT / "staff.json")
    assert "jjscheel" in staff["github"] and "jeff scheel" in staff["names"]
