"""build_facts(): the facts are computed here, in reading order, never by the model."""
import re

import pytest

from conftest import ROWS, TODAY, make_row


def facts_for(ais, name, **override):
    fields = dict(ROWS[name])
    fields.update(override)
    return ais.build_facts(make_row(**fields), TODAY)


def test_planning_with_nothing_set(ais):
    assert facts_for(ais, "planning_empty") == [
        "The specification is in Planning",
        "No projected ratification quarter is set yet",
    ]


def test_development_on_track(ais):
    assert facts_for(ais, "development_on_track") == [
        "The specification is in Development",
        "This phase started on 2026-03-09",
        "The projected ratification quarter is 27Q1",
        "It matches the baseline 27Q1",
        "The projection has not changed in the last 90 days",
        "The next approval is Development complete, projected for 2026-12-25",
        "The projected dates were last set on 2026-03-09",
        "The last GitHub contribution was on 2026-10-16",
    ]


def test_stabilization_exposed_with_recent_slip(ais):
    assert facts_for(ais, "stabilization_exposed_slip") == [
        "The specification is in Stabilization",
        "This phase started on 2026-06-22",
        "Development started on 2025-01-10",
        "The projected ratification quarter is 27Q2",
        "It is 2 quarters behind the baseline 26Q4",
        "On 2026-10-01 the projection moved later, from 27Q1 to 27Q2",
        "The next approval is Stabilization approval, projected for 2027-02-01",
        "ARC review: Approval In Progress",
    ]


def test_ratification_ready_late_against_baseline(ais):
    assert facts_for(ais, "ratification_ready_late") == [
        "The specification is in Ratification-Ready",
        "The projected ratification quarter is 26Q4",
        "It is 4 quarters behind the baseline 25Q4",
        "On 2026-09-01 the projection moved earlier, from 27Q1 to 26Q4",
        "ARC review: Approved",
        "Public review: Public Review Done",
        "TSC approval: Not Started",
    ]


def test_cancelled_is_one_fact(ais):
    assert facts_for(ais, "cancelled") == ["The specification was cancelled"]


def test_fast_track(ais):
    assert facts_for(ais, "fast_track_freeze") == [
        "The specification is in Freeze, following the fast-track process",
        "This phase started on 2026-07-08",
        "Development started on 2024-03-08",
        "The projected ratification quarter is 27Q1",
        "The next approval is Freeze approval, projected for 2026-12-19",
    ]


def test_inception(ais):
    assert facts_for(ais, "inception") == [
        "The specification is in Inception",
        "The projected ratification quarter is 27Q2",
    ]


@pytest.mark.parametrize("name", list(ROWS))
def test_no_relative_ages(ais, name):
    """Summaries are cached; "N days ago" would go stale."""
    for fact in facts_for(ais, name):
        assert not re.search(r"\bdays? ago\b|\bago\b", fact), fact


@pytest.mark.parametrize("name", list(ROWS))
def test_facts_pass_their_own_checks(ais, name):
    """The plain template must always be a valid summary, or the fallback breaks the rule."""
    facts = facts_for(ais, name)
    text = ais.template(facts)
    assert ais.valid(text, facts), text
    assert not ais.OPINION.search(text)


def test_no_phase_start_or_schedule_status(ais):
    """Neither the Phase Start quarter nor the Ratification Progress status is a fact."""
    for name in ROWS:
        for fact in facts_for(ais, name):
            assert "phase start quarter" not in fact and "schedule status" not in fact
            assert "replanned since entering" not in fact


@pytest.mark.parametrize("changed,expected", [
    ("2026-07-21", "On 2026-07-21 the projection moved later, from 27Q1 to 27Q2"),  # 90 days: inside
    ("2026-07-20", "The projection has not changed in the last 90 days"),  # 91 days: outside
    ("not a date", "The projection has not changed in the last 90 days"),
])
def test_trend_window(ais, changed, expected):
    facts = facts_for(ais, "stabilization_exposed_slip", **{"Target Changed": changed})
    assert expected in facts


def test_trend_same_quarter_is_no_change(ais):
    facts = facts_for(ais, "stabilization_exposed_slip", **{"Previous Target Ratification Quarter": "27Q2"})
    assert "The projection has not changed in the last 90 days" in facts
    assert not any("moved" in f for f in facts)


def test_bad_dates_are_dropped_not_guessed(ais):
    facts = facts_for(ais, "development_on_track", **{
        "Phase Started": "soon", "Latest Replanning": "Not Set", "Last Contribution": "",
    })
    assert not any(f.startswith(("This phase started", "The projected dates", "The last GitHub")) for f in facts)


def test_unparsable_quarter_gap_is_dropped(ais):
    facts = facts_for(ais, "development_on_track", **{"Baseline Ratification Quarter": "2027"})
    assert not any("baseline" in f for f in facts)
    assert None not in facts


def test_github_snapshot_follows_the_last_contribution(ais):
    row = make_row(**ROWS["development_on_track"])
    facts = ais.build_facts(row, TODAY, "9 commits, 10 PRs by 2 people in riscv/riscv-fast-interrupt")
    assert facts[-2:] == [
        "The last GitHub contribution was on 2026-10-16",
        "In the last 90 days, GitHub had 9 commits, 10 PRs by 2 people in riscv/riscv-fast-interrupt",
    ]


@pytest.mark.parametrize("text,expected", [
    ("0 commits by 0 people in riscv/riscv-zilx", "In the last 90 days, riscv/riscv-zilx had no commits or PRs from members"),
    ("", None),
    (None, None),
])
def test_github_snapshot_edge_cases(ais, text, expected):
    facts = ais.build_facts(make_row(**ROWS["development_on_track"]), TODAY, text)
    assert facts[-1] == expected if expected else facts[-1].startswith("The last GitHub")


def test_main_reads_activity_next_to_the_output(ais, rows, run_main):
    run_main.out_path.parent.mkdir(parents=True, exist_ok=True)
    (run_main.out_path.parent / "activity.json").write_text(
        '{"specs": {"RVS-2": {"text": {"github": "1 commit by 1 person in riscv/x"}}}}')
    out, _ = run_main([rows["development_on_track"]])
    assert out["RVS-2"]["source"] == "ai"
    facts = ais.build_facts(rows["development_on_track"], TODAY, "1 commit by 1 person in riscv/x")
    assert out["RVS-2"]["hash"] == ais.hashlib.sha256(
        ais.json.dumps([ais.MODEL_ID, ais.SYSTEM, facts]).encode()).hexdigest()[:16]
