"""valid(): the model may only combine the facts; anything else falls back to the template."""
import pytest

FACTS = [
    "The specification is in Stabilization",
    "This phase started on 2026-06-22",
    "Development started on 2025-01-10",
    "The projected ratification quarter is 27Q2",
    "It is 2 quarters behind the baseline 26Q4",
    "It has not been replanned since entering this phase",
    "The schedule status is Exposed",
    "On 2026-10-01 the projection moved later, from 27Q1 to 27Q2",
    "The next approval is Stabilization approval, projected for 2027-02-01",
    "ARC review: Approval In Progress",
]

GOOD = (
    "The specification is in Stabilization, which started on 2026-06-22, and development started on 2025-01-10. "
    "The projected ratification quarter is 27Q2, 2 quarters behind the baseline 26Q4, and it has not been "
    "replanned since entering this phase. The schedule status is Exposed. On 2026-10-01 the projection moved "
    "later, from 27Q1 to 27Q2. The next approval is Stabilization approval, projected for 2027-02-01. "
    "ARC review: Approval In Progress."
)


def test_accepts_faithful_rewrite(ais):
    assert ais.valid(GOOD, FACTS)


def test_accepts_template(ais):
    assert ais.valid(ais.template(FACTS), FACTS)


@pytest.mark.parametrize("text", [
    GOOD + " It has 3 open pull requests.",                       # invented number
    GOOD + " The next milestone is in 27Q4.",                      # invented quarter
    GOOD + " A meeting is set for 2026-11-05.",                    # invented date
    GOOD.replace(", 2 quarters behind the baseline 26Q4", ""),    # dropped quarter
    GOOD.replace(", and development started on 2025-01-10", ""),  # dropped date
    GOOD.replace(" The schedule status is Exposed.", ""),          # dropped status
], ids=["invented-number", "invented-quarter", "invented-date",
        "dropped-quarter", "dropped-date", "dropped-status"])
def test_rejects_changed_facts(ais, text):
    assert not ais.valid(text, FACTS)


@pytest.mark.parametrize("word", [
    "a risk", "at risk", "delayed", "a delay", "significant", "significantly", "likely", "unlikely",
    "ongoing", "recent", "recently", "concerning", "lagging", "expected", "however", "still",
])
def test_rejects_judging_words(ais, word):
    assert not ais.valid(GOOD + f" This is {word}.", FACTS)


def test_rejects_judging_word_in_any_case(ais):
    assert not ais.valid("Recently, " + GOOD, FACTS)


def test_rejects_riscv(ais):
    assert not ais.valid(GOOD.replace("The specification is", "The RISC-V specification is", 1), FACTS)


def test_rejects_em_dash(ais):
    assert not ais.valid(GOOD.replace(", which started", " — which started"), FACTS)


@pytest.mark.parametrize("text", [GOOD[:-1], GOOD[:-1] + " ", GOOD.rsplit(" ", 3)[0]])
def test_rejects_cut_off_text(ais, text):
    assert not ais.valid(text, FACTS)


def test_accepts_trailing_whitespace_after_period(ais):
    assert ais.valid(GOOD + "  ", FACTS)


def test_rejects_over_long_text(ais):
    filler = " This sentence repeats the same plain words again." * 20
    assert len((GOOD + filler).split()) > 140
    assert not ais.valid(GOOD + filler, FACTS)


@pytest.mark.parametrize("text", ["", None])
def test_rejects_empty(ais, text):
    assert not ais.valid(text, FACTS)


def test_status_check_ignores_case(ais):
    assert ais.valid(GOOD.replace("is Exposed", "is exposed"), FACTS)


def test_dropped_late_status_not_hidden_by_moved_later(ais):
    """"Late" must be found as a word, not inside "later"."""
    facts = [
        "The specification is in Freeze",
        "The projected ratification quarter is 27Q1",
        "The schedule status is Late",
        "On 2026-09-01 the projection moved later, from 26Q4 to 27Q1",
    ]
    assert ais.valid(ais.template(facts), facts)
    dropped = ("The specification is in Freeze. The projected ratification quarter is 27Q1. "
               "On 2026-09-01 the projection moved later, from 26Q4 to 27Q1.")
    assert not ais.valid(dropped, facts)


# digits(): number words become digits before the token check.

@pytest.mark.parametrize("text,expected", [
    ("one quarter", "1 quarter"),
    ("One quarter behind", "1 quarter behind"),
    ("TWO quarters", "2 quarters"),
    ("twelve days", "12 days"),
    ("zero", "0"),
    ("none of someone", "none of someone"),
    ("thirteen", "thirteen"),
    ("27Q1", "27Q1"),
])
def test_digits(ais, text, expected):
    assert ais.digits(text) == expected


def test_number_word_matching_a_fact_is_accepted(ais):
    text = ais.digits(GOOD.replace("2 quarters behind", "two quarters behind"))
    assert ais.valid(text, FACTS)


def test_number_word_not_in_facts_is_rejected(ais):
    text = ais.digits(GOOD.replace("2 quarters behind", "three quarters behind"))
    assert not ais.valid(text, FACTS)


def test_single_quarter_written_as_word(ais):
    facts = ["The projected ratification quarter is 27Q2", "It is 1 quarter behind the baseline 27Q1"]
    text = ais.digits("The projected ratification quarter is 27Q2, one quarter behind the baseline 27Q1.")
    assert text == "The projected ratification quarter is 27Q2, 1 quarter behind the baseline 27Q1."
    assert ais.valid(text, facts)


def test_tokens(ais):
    # A date is one token; its parts are not counted again.
    assert ais.tokens("On 2026-10-01, 27Q1 to 27Q2, 2 quarters") == {"2026-10-01", "27Q1", "27Q2", "2"}


def test_prompt_states_the_rule(ais):
    """The system prompt must keep telling the model not to add, drop, interpret or conclude."""
    for phrase in ("do not add", "drop", "interpret", "judge", "conclusions", "em-dash", "in the given order"):
        assert phrase in ais.SYSTEM
