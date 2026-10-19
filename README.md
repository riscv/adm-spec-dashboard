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

For local tests, the dev server will try to load `specs_20260107_182508.csv` from the repo root before falling back to the deployed `web-ui/public/latest.csv`.

If you deploy to a custom domain, set `BASE_URL=/` in the build or update `web-ui/vite.config.js`.
