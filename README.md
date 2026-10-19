# bod-report
Automation to collect Jira issues to generate the BoD reports

## Web UI (Vite)
The static dashboard lives in `web-ui/` and is deployed with GitHub Pages.

Local dev:
```bash
cd web-ui
npm install
npm run dev
```

Build:
```bash
cd web-ui
npm run build
```

For local tests, the dev server will try to load `specs_20260107_182508.csv` from the repo root before falling back to the latest release asset.

If you deploy to a custom domain, set `BASE_URL=/` in the build or update `web-ui/vite.config.js`.

## History and weekly digest
The `history` job of the Daily Data Fetch workflow stores one snapshot per spec per day (UTC) in the DynamoDB table `adm-spec-dashboard-history` (us-east-1; key `spec` + `date`, re-runs on the same day overwrite). Cancelled specs are skipped. An item with `spec = "_index"` lists the specs stored each day.

`weekly-digest.py` compares the latest snapshot with the one closest to 7 days earlier and writes `web-ui/public/changes.json`: changes in phase, projected quarter, ratification progress and next approval date, plus specs that appeared or disappeared. With fewer than 2 snapshots it writes an empty list and a `note`.

The job assumes the IAM role in the repo variable `AWS_HISTORY_ROLE_ARN` through GitHub OIDC. The role trusts `main` only and may only PutItem, BatchWriteItem, Query and GetItem on that table, so runs from other branches skip the history. Local run (with AWS credentials):
```bash
python snapshot-history.py web-ui/public/latest.csv
python weekly-digest.py --out web-ui/public/changes.json
```
