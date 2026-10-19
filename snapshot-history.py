"""Store today's state of every spec in the DynamoDB history table.

One item per spec per day (UTC), keyed by spec (the RVS key) and date
(YYYY-MM-DD). Writing again on the same day overwrites that day's item, so
the three daily runs leave the last state of the day. Cancelled specs are
skipped.

An extra item with spec "_index" lists the specs written that day. The role
may only Query and GetItem (no Scan), so weekly-digest.py reads this index to
find which days have a snapshot and which specs each day held, including
specs that have since left the CSV.

Usage: python snapshot-history.py CSV [--date YYYY-MM-DD] [--run-id ID]
Env:   HISTORY_TABLE (default below), AWS_REGION, plus normal AWS credentials.
"""
import argparse
import csv
import datetime as dt
import os
import random
import sys
import time

import boto3

TABLE = os.environ.get("HISTORY_TABLE", "adm-spec-dashboard-history")
# boto3 reads only AWS_DEFAULT_REGION; accept AWS_REGION too.
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
INDEX_KEY = "_index"
BATCH = 25  # BatchWriteItem limit
MAX_ATTEMPTS = 8

# item attribute -> CSV column
FIELDS = {
    "summary": "Summary",
    "status": "Status",
    "baseline_q": "Baseline Ratification Quarter",
    "phase_start_q": "Plan Ratification Quarter",
    "projected_q": "Target Ratification Quarter",
    "progress": "Ratification Progress",
    "previous_progress": "Previous Ratification Progress",
    "next_approval": "Next Approval",
    "next_approval_date": "Next Approval Date",
    "latest_replanning": "Latest Replanning",
    "phase_started": "Phase Started",
    "last_contribution": "Last Contribution",
}


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, quotechar="'"))


def spec_key(row):
    return (row.get("Jira URL") or "").rstrip("/").rsplit("/", 1)[-1].strip()


def read_run_id(csv_path):
    meta = csv_path + ".meta"
    try:
        with open(meta, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("run_id="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return os.environ.get("GITHUB_RUN_ID", "local")


def build_items(rows, date, run_id):
    items = {}
    for row in rows:
        if (row.get("Status") or "").strip() == "Cancelled":
            continue
        spec = spec_key(row)
        if not spec:
            continue
        item = {"spec": {"S": spec}, "date": {"S": date}, "run_id": {"S": run_id}}
        for attr, col in FIELDS.items():
            item[attr] = {"S": (row.get(col) or "").strip()}
        items[spec] = item  # a duplicate key would fail the whole batch
    return items


def batch_write(client, items):
    for start in range(0, len(items), BATCH):
        pending = [{"PutRequest": {"Item": i}} for i in items[start:start + BATCH]]
        attempt = 0
        while pending:
            attempt += 1
            out = client.batch_write_item(RequestItems={TABLE: pending})
            pending = out.get("UnprocessedItems", {}).get(TABLE, [])
            if not pending:
                break
            if attempt >= MAX_ATTEMPTS:
                raise RuntimeError(f"{len(pending)} items still unprocessed after {attempt} attempts")
            time.sleep(min(10, 0.2 * 2 ** attempt) + random.random() * 0.2)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv")
    ap.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()
    dt.date.fromisoformat(args.date)  # validate

    run_id = args.run_id or read_run_id(args.csv)
    items = build_items(read_rows(args.csv), args.date, run_id)
    if not items:
        print("No specs to store.", file=sys.stderr)
        return 1

    index = {
        "spec": {"S": INDEX_KEY}, "date": {"S": args.date}, "run_id": {"S": run_id},
        "specs": {"SS": sorted(items)}, "count": {"N": str(len(items))},
    }
    client = boto3.client("dynamodb", region_name=REGION)
    batch_write(client, list(items.values()) + [index])
    print(f"Stored {len(items)} specs for {args.date} in {TABLE} (run {run_id}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
