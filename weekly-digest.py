"""Write the weekly "what changed" digest from the DynamoDB history table.

Takes the latest snapshot in the last 8 days and the snapshot closest to
7 days before it, and lists, per spec, the changes in phase (status),
projected quarter, ratification progress and next approval date, plus the
specs that appeared or disappeared between the two. Facts only: each entry is
a field with its old and new value, nothing is interpreted.

A spec "disappeared" when it is in the older snapshot and not in the newer
one, which happens when it was cancelled (cancelled specs are not stored) or
left the dashboard CSV.

Output (web-ui/public/changes.json):
  {"generated": "YYYY-MM-DD", "from": "YYYY-MM-DD", "to": "YYYY-MM-DD",
   "changes": [{"spec", "summary", "field", "from", "to"}, ...]}
With fewer than 2 snapshots, "from"/"to" may be null, "changes" is empty and
a "note" says why.

Usage: python weekly-digest.py [--out web-ui/public/changes.json] [--today YYYY-MM-DD]
Env:   HISTORY_TABLE (default below), AWS_REGION, plus normal AWS credentials.
"""
import argparse
import datetime as dt
import json
import os
import sys

import boto3
from boto3.dynamodb.conditions import Key

TABLE = os.environ.get("HISTORY_TABLE", "adm-spec-dashboard-history")
# boto3 reads only AWS_DEFAULT_REGION; accept AWS_REGION too.
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
INDEX_KEY = "_index"
WINDOW_DAYS = 8
TARGET_GAP_DAYS = 7
# item attribute -> field name in changes.json
COMPARED = {
    "status": "status",
    "projected_q": "projected_q",
    "progress": "progress",
    "next_approval_date": "next_approval_date",
}


def query_all(table, spec, start, end):
    kwargs = {"KeyConditionExpression": Key("spec").eq(spec) & Key("date").between(start, end)}
    items = []
    while True:
        out = table.query(**kwargs)
        items.extend(out.get("Items", []))
        if "LastEvaluatedKey" not in out:
            return items
        kwargs["ExclusiveStartKey"] = out["LastEvaluatedKey"]


def pick_dates(dates):
    """Return (older, newer): the newest date and the one closest to 7 days before it."""
    newest = max(dates)
    target = dt.date.fromisoformat(newest) - dt.timedelta(days=TARGET_GAP_DAYS)
    older = [d for d in dates if d != newest]
    # Closest to the target; on a tie take the earlier day (the longer span).
    best = min(older, key=lambda d: (abs((dt.date.fromisoformat(d) - target).days), d))
    return best, newest


def digest(table, today):
    start = (today - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    end = today.isoformat()
    index = {i["date"]: set(i.get("specs", set())) for i in query_all(table, INDEX_KEY, start, end)}
    out = {"generated": end, "from": None, "to": None, "changes": []}
    if len(index) < 2:
        out["note"] = (f"Fewer than 2 snapshots between {start} and {end} "
                       f"({len(index)} found); no comparison yet.")
        return out

    old_date, new_date = pick_dates(sorted(index))
    out["from"], out["to"] = old_date, new_date
    old_specs, new_specs = index[old_date], index[new_date]

    changes = []
    for spec in sorted(old_specs | new_specs, key=sort_key):
        snaps = {i["date"]: i for i in query_all(table, spec, old_date, new_date)}
        old, new = snaps.get(old_date), snaps.get(new_date)
        summary = (new or old or {}).get("summary", "")
        if spec not in old_specs or old is None:
            if new is not None:
                changes.append(entry(spec, summary, "presence", "absent", "present"))
            continue
        if spec not in new_specs or new is None:
            changes.append(entry(spec, summary, "presence", "present", "absent"))
            continue
        for attr, field in COMPARED.items():
            a, b = old.get(attr, ""), new.get(attr, "")
            if a != b:
                changes.append(entry(spec, summary, field, a, b))
    out["changes"] = changes
    return out


def entry(spec, summary, field, old, new):
    return {"spec": spec, "summary": summary, "field": field, "from": old, "to": new}


def sort_key(spec):
    num = spec.rsplit("-", 1)[-1]
    return (spec.rsplit("-", 1)[0], int(num) if num.isdigit() else 0, spec)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="web-ui/public/changes.json")
    ap.add_argument("--today", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    args = ap.parse_args()

    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
    result = digest(table, dt.date.fromisoformat(args.today))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"Wrote {args.out}: {len(result['changes'])} changes"
          + (f" ({result['from']} -> {result['to']})" if result["to"] else f"; {result.get('note')}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
