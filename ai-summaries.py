"""Write a short, human readable summary for each spec in the dashboard CSV.

The model only rewrites facts into sentences; it must not interpret them.
Every fact (quarter gaps, trend, activity age) is computed here, in the order
it should be read. A summary is rejected, and the facts are joined as plain
sentences instead, when it adds a quarter, date or number, drops one, drops
the schedule status, or uses a word that judges or interprets.

Summaries are cached in summaries.json by a hash of each spec's facts, the
prompt and the model, so the model is only called when one of them changed.

A spec developed by a group (the Jira Develops link) also gets a "group"
entry: the group's Activity Level and the dates it is computed from, with the
rule that level stands for, so the dashboard can explain it.

Usage: python ai-summaries.py CSV [--out web-ui/public/summaries.json]
       [--only RVS-1,RVS-2] [--no-model]
Env:   BEDROCK_MODEL_ID (default below), AWS_REGION, plus normal AWS credentials.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sys

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "qwen.qwen3-next-80b-a3b")
TREND_WINDOW_DAYS = 90  # same as the dashboard
NOT_SET = ("", "Not Set", "Not Set Yet")

SYSTEM = (
    "Rewrite the facts as 3 to 6 short, plain sentences for a dashboard. "
    "Keep every fact, in the given order. Only combine and smooth the wording: do not add, "
    "drop, interpret, judge or explain anything, and do not draw conclusions. "
    "Keep quarters (like 27Q2), dates and status names exactly as written, and numbers as digits. "
    "Do not use the em-dash. Return only the sentences."
)

# Words that judge or interpret; the facts never use them.
OPINION = re.compile(
    r"\b(risk\w*|concern\w*|significant\w*|delay\w*|lag\w*|miss\w*|beat\w*|exposing|ongoing|"
    r"recent\w*|only|still|despite|however|unfortunately|good|bad|healthy|likely|unlikely|"
    r"expected|should|may|might|suggest\w*|indicat\w*|appear\w*|seem\w*|"
    r"not (provided|given|available)|unknown)\b", re.I)


def quarter_index(q):
    m = re.fullmatch(r"(\d\d)Q([1-4])", (q or "").strip())
    return int(m.group(1)) * 4 + int(m.group(2)) - 1 if m else None


def quarters(n):
    return f"{n} quarter" + ("" if n == 1 else "s")


def phase_name(status):
    return re.sub(r"^Specification\s+(in\s+|Under\s+)?", "", status or "").strip() or "Unknown"


def gap_text(target, ref, ref_name):
    t, r = quarter_index(target), quarter_index(ref)
    if t is None or r is None:
        return None
    d = t - r
    if d == 0:
        return f"It matches the {ref_name} {ref}"
    word = "behind" if d > 0 else "ahead of"
    return f"It is {quarters(abs(d))} {word} the {ref_name} {ref}"


def build_facts(row, today):
    """Return the facts for one spec as plain sentences, in reading order."""
    status = row["Status"]
    phase = phase_name(status)
    if status == "Cancelled":
        return ["The specification was cancelled"]
    facts = [f"The specification is in {phase}" +
             (", following the fast-track process" if row.get("Fast Track") == "Yes" else "")]
    started = dated(row.get("Phase Started", ""), today)
    if started:
        facts.append(f"This phase started on {started}")
    dev = dated(row.get("Development Started", ""), today)
    if dev and phase != "Development":
        facts.append(f"Development started on {dev}")

    baseline = row.get("Baseline Ratification Quarter", "")
    phase_start = row.get("Plan Ratification Quarter", "")
    target = row.get("Target Ratification Quarter", "")
    progress = row.get("Ratification Progress", "")

    if target in NOT_SET:
        facts.append("No projected ratification quarter is set yet")
    else:
        facts.append(f"The projected ratification quarter is {target}")
        if baseline not in NOT_SET:
            facts.append(gap_text(target, baseline, "baseline"))
        if phase_start not in NOT_SET:
            facts.append(gap_text(target, phase_start, "phase start quarter"))
        elif phase in ("Development", "Stabilization", "Freeze", "Ratification-Ready", "Publication"):
            facts.append("It has not been replanned since entering this phase")
    if progress not in NOT_SET:
        facts.append(f"The schedule status is {progress}")

    prev, changed = row.get("Previous Target Ratification Quarter", ""), row.get("Target Changed", "")
    if target not in NOT_SET and changed:
        try:
            age = (today - dt.date.fromisoformat(changed[:10])).days
        except ValueError:
            age = None
        pi, ti = quarter_index(prev), quarter_index(target)
        if age is not None and age <= TREND_WINDOW_DAYS and pi is not None and ti is not None and pi != ti:
            way = "moved later" if ti > pi else "moved earlier"
            facts.append(f"On {changed[:10]} the projection {way}, from {prev} to {target}")
        else:
            facts.append(f"The projection has not changed in the last {TREND_WINDOW_DAYS} days")

    approval, approval_date = row.get("Next Approval", ""), row.get("Next Approval Date", "")
    if approval and approval_date:
        facts.append(f"The next approval is {approval}, projected for {approval_date}")
    replanned = dated(row.get("Latest Replanning", ""), today)
    if replanned:
        facts.append(f"The projected dates were last set on {replanned}")

    for col, label in (("ARC Review Status", "ARC review"), ("Public Review Status", "Public review"),
                       ("TSC Approval Status", "TSC approval"), ("BoD Approval Status", "Board approval")):
        if row.get(col, "") not in NOT_SET:
            facts.append(f"{label}: {row[col]}")

    last = dated(row.get("Last Contribution", ""), today)
    if last:
        facts.append(f"The last repository activity was on {last}")
    return [f for f in facts if f]


# The three dates the Activity Level is computed from, as get-specs-data.py
# names them in the "Group Activity Signals" column.
GROUP_SIGNALS = (
    ("Confluence", "Its Confluence space was last updated on {}", "No Confluence update is recorded"),
    ("Mailing list", "Its mailing list last had a message on {}", "No mailing list message is recorded"),
    ("Repository", "Its repository was last updated on {}", "No repository update is recorded"),
)


def level_rule(level, group_type):
    """What the level means, as riscv-tech-committee-report computes it."""
    signals = "its Confluence space, mailing list and repository"
    if level == "Very Active":
        return f"Very Active means {signals} were all updated within 15 days"
    if level == "Active":
        gt = (group_type or "").lower()
        if "task group" in gt:
            main = "the repository"
        elif any(k in gt for k in ("special interest", "isa committee", "steering")):
            main = "the mailing list"
        else:
            main = ""
        rule = f"Active means at least 2 of {signals} were updated within 45 days"
        return rule + (f", or {main}, the main signal for this type of group, was" if main else "")
    if level == "Low":
        return f"Low means at least 1 of {signals} was updated within 60 days"
    if level == "Stalled":
        return f"Stalled means none of {signals} was updated within 60 days"
    return None


def build_group_facts(row, today):
    """The facts behind the developing group's Activity Level; None without a group."""
    group = (row.get("Group") or "").strip()
    if not (row.get("Group Key") or "").strip() or not group:
        return None
    facts = [f"The specification is developed by the {group}"]
    level = (row.get("Group Activity Level") or "").strip()
    rule = level_rule(level, row.get("Group Type", ""))
    if not rule:
        # Not an Active group (e.g. Archived): get-specs-data.py writes its status.
        return facts + ([f"The group is {level}"] if level else ["No activity level is set for the group"])
    facts.append(f"Its activity level is {level}")
    signals = dict(p.split("=", 1) for p in (row.get("Group Activity Signals") or "").split(";") if "=" in p)
    # No dates at all means the activity report could not be read: say
    # nothing about them rather than "none recorded".
    if signals:
        for name, said, missing in GROUP_SIGNALS:
            when = dated(signals.get(name, ""), today)
            facts.append(said.format(when) if when else missing)
    facts.append(rule)
    return facts


def dated(value, today):
    """The ISO date of a date or timestamp; '' when unset or bad.

    No "N days ago": summaries are cached, so relative ages would go stale.
    The dashboard computes live ages next to the summary instead.
    """
    try:
        return dt.date.fromisoformat((value or "")[:10]).isoformat()
    except ValueError:
        return ""


def template(facts):
    return ". ".join(facts) + "."


def tokens(text):
    """Quarters, dates and bare numbers: what a summary must not invent."""
    return set(re.findall(r"\b\d\dQ[1-4]\b|\b\d{4}-\d\d-\d\d\b|\b\d+\b", text))


NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}


def digits(text):
    """Write number words as digits, so the token check below sees them."""
    return re.sub(r"\b(%s)\b" % "|".join(NUMBER_WORDS),
                  lambda m: NUMBER_WORDS[m.group(1).lower()], text, flags=re.I)


def valid(summary, facts):
    if not summary or "\u2014" in summary or len(summary.split()) > 140:
        return False
    # A cut-off reply (token limit) does not end a sentence.
    if not summary.rstrip().endswith("."):
        return False
    if "RISC-V" in summary or OPINION.search(summary):
        return False
    given = tokens(" ".join(facts))
    said = tokens(summary)
    # Nothing added, and no quarter or date dropped.
    keep = {t for t in given if "Q" in t or "-" in t}
    if not said <= given or not keep <= said:
        return False
    # The schedule status (On Track, Exposed, ...) and the group's activity
    # level must be kept word for word, as whole words: "Late" must not be
    # found inside "moved later".
    status = [f[len(p):] for f in facts for p in KEEP_AS_WRITTEN if f.startswith(p)]
    return all(re.search(r"\b%s\b" % re.escape(st), summary, re.I) for st in status)


KEEP_AS_WRITTEN = ("The schedule status is ", "Its activity level is ")


def ask_model(client, facts):
    resp = client.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM}],
        messages=[{"role": "user", "content": [{"text": "Facts:\n- " + "\n- ".join(facts)}]}],
        inferenceConfig={"maxTokens": 500, "temperature": 0},
    )
    text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
    # Qwen3 models may wrap their reasoning in <think> tags.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    return " ".join(text.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default="web-ui/public/summaries.json")
    ap.add_argument("--only", default="", help="comma separated RVS keys")
    ap.add_argument("--no-model", action="store_true", help="templates only, no Bedrock calls")
    args = ap.parse_args()

    today = dt.date.today()
    only = {k.strip() for k in args.only.split(",") if k.strip()}
    try:
        cache = json.load(open(args.out))
    except (OSError, ValueError):
        cache = {}

    client = None
    if not args.no_model:
        import boto3
        region = os.environ.get("AWS_REGION") or "us-east-1"
        client = boto3.Session(region_name=region).client("bedrock-runtime")

    out, counts = {}, {"calls": 0, "rejected": 0}

    def summarize(label, facts, old, use_model):
        # The prompt and model are part of the hash, so changing either regenerates all.
        digest = hashlib.sha256(json.dumps([MODEL_ID, SYSTEM, facts]).encode()).hexdigest()[:16]
        if old and old.get("hash") == digest and (old.get("source") == "ai" or client is None):
            return old
        entry = {"hash": digest, "summary": template(facts), "source": "template", "generated": today.isoformat()}
        if client is not None and use_model:
            counts["calls"] += 1
            try:
                text = digits(ask_model(client, facts))
            except Exception as e:  # keep the template; the dashboard must still refresh
                print(f"{label}: Bedrock call failed: {e}", file=sys.stderr)
                text = ""
            if valid(text, facts):
                entry.update(summary=text, source="ai", model=MODEL_ID)
            else:
                counts["rejected"] += 1
                print(f"{label}: rejected model output: {text!r}", file=sys.stderr)
        if only:
            print(f"\n{label}\n  facts: " + "\n         ".join(facts) + f"\n  [{entry['source']}] {entry['summary']}")
        return entry

    # get-specs-data.py writes the CSV with ' as the quote character.
    for row in csv.DictReader(open(args.csv, newline=""), quotechar="'"):
        key = row["Jira URL"].rsplit("/", 1)[-1]
        if only and key not in only:
            continue
        old = cache.get(key) or {}
        spec_old = {k: v for k, v in old.items() if k != "group"}
        entry = dict(summarize(key, build_facts(row, today), spec_old, row["Status"] != "Cancelled"))
        group_facts = build_group_facts(row, today)
        if group_facts:
            # Without a level there is nothing to explain; the template says so.
            has_level = any(f.startswith("Its activity level is ") for f in group_facts)
            entry["group"] = summarize(f"{key} group", group_facts, old.get("group"), has_level)
        out[key] = entry
    calls, rejected = counts["calls"], counts["rejected"]

    if not only:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as fh:
            json.dump(out, fh, indent=1, sort_keys=True)
    print(f"{len(out)} specs, {calls} model calls, {rejected} rejected", file=sys.stderr)


if __name__ == "__main__":
    main()
