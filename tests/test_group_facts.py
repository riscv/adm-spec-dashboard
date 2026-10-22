"""The developing group's Activity Level: its facts, and its own cache entry."""
import pytest

from conftest import ROWS, TODAY, FakeClient, make_row

COVE = {
    "Group Key": "RVG-76",
    "Group": "CoVE TG",
    "Group Type": "Task Group (TG)",
    "Group Activity Level": "Very Active",
    "Group Activity Signals": "Confluence=2026-10-06;Mailing list=2026-10-19;Repository=2026-10-19",
}


def group_row(key="RVS-2", **group):
    fields = dict(ROWS["development_on_track"])
    fields.update(group)
    return make_row(key, **fields)


def test_very_active_task_group(ais):
    assert ais.build_group_facts(group_row(**COVE), TODAY) == [
        "The specification is developed by the CoVE TG",
        "Its activity level is Very Active",
        "Its Confluence space was last updated on 2026-10-06",
        "Its mailing list last had a message on 2026-10-19",
        "Its repository was last updated on 2026-10-19",
        "Very Active means its Confluence space, mailing list and repository were all updated within 15 days",
    ]


@pytest.mark.parametrize("group_type, clause", [
    ("Task Group (TG)", ", or the repository, the main signal for this type of group, was"),
    ("Special Interest Group (SIG)", ", or the mailing list, the main signal for this type of group, was"),
    ("Horizontal Committee (HC)", ""),
])
def test_active_names_the_main_signal(ais, group_type, clause):
    facts = ais.build_group_facts(group_row(**{**COVE, "Group Activity Level": "Active", "Group Type": group_type}), TODAY)
    assert facts[-1] == ("Active means at least 2 of its Confluence space, mailing list and repository "
                         "were updated within 45 days" + clause)


def test_missing_dates_are_said(ais):
    facts = ais.build_group_facts(group_row(**{**COVE, "Group Activity Level": "Low",
                                               "Group Activity Signals": "Mailing list=2026-10-14"}), TODAY)
    assert facts[2:] == [
        "No Confluence update is recorded",
        "Its mailing list last had a message on 2026-10-14",
        "No repository update is recorded",
        "Low means at least 1 of its Confluence space, mailing list and repository was updated within 60 days",
    ]


def test_no_report_means_no_date_facts(ais):
    facts = ais.build_group_facts(group_row(**{**COVE, "Group Activity Signals": ""}), TODAY)
    assert [f for f in facts if "recorded" in f or "last" in f] == []
    assert facts[1] == "Its activity level is Very Active"


def test_archived_group(ais):
    facts = ais.build_group_facts(group_row(**{**COVE, "Group Activity Level": "Archived"}), TODAY)
    assert facts == ["The specification is developed by the CoVE TG", "The group is Archived"]


def test_no_group(ais):
    assert ais.build_group_facts(group_row(), TODAY) is None


@pytest.mark.parametrize("level", ["Very Active", "Active", "Low", "Stalled"])
def test_group_facts_pass_their_own_checks(ais, level):
    facts = ais.build_group_facts(group_row(**{**COVE, "Group Activity Level": level}), TODAY)
    assert ais.valid(ais.template(facts), facts)


def test_dropping_the_level_is_rejected(ais):
    facts = ais.build_group_facts(group_row(**{**COVE, "Group Activity Level": "Stalled"}), TODAY)
    # The rule sentence also names the level, so drop both.
    text = ais.template([f for f in facts if "Stalled" not in f])
    assert not ais.valid(text, facts)


def test_group_entry_is_nested_and_cached(ais, run_main):
    rows = [group_row(**COVE)]
    out, client = run_main(rows)
    assert len(client.calls) == 2  # the spec and its group
    group = out["RVS-2"]["group"]
    assert group["source"] == "ai" and group["model"] == ais.MODEL_ID
    # The spec entry is unchanged by the group: same hash as without one.
    alone, _ = run_main([group_row()], client=FakeClient())
    assert "group" not in alone["RVS-2"]
    assert alone["RVS-2"]["hash"] == out["RVS-2"]["hash"]


def test_level_change_calls_only_for_the_group(run_main):
    run_main([group_row(**COVE)])
    _, client = run_main([group_row(**{**COVE, "Group Activity Level": "Active"})])
    (call,) = client.calls
    assert "Its activity level is Active" in call["messages"][0]["content"][0]["text"]


def test_archived_group_never_calls_the_model(run_main):
    out, client = run_main([group_row(**{**COVE, "Group Activity Level": "Archived"})])
    assert len(client.calls) == 1  # the spec only
    assert out["RVS-2"]["group"]["source"] == "template"
    assert out["RVS-2"]["group"]["summary"] == "The specification is developed by the CoVE TG. The group is Archived."
