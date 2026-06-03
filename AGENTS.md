# AGENTS.md — ETF Watcher Bug Fix

## Backpressure Commands (run after each fix)

```bash
# Python syntax check
python3 -c "import ast; [ast.parse(open(f).read()) for f in ['scripts/etf_v7_threefactor.py','scripts/gen_dashboard.py','scripts/etf_data_store.py']]" && echo "SYNTAX OK"

# JS unit tests
node scripts/test_sentiment_functions.js

# Health check (requires network)
node scripts/health_check.js

# Smoke run (no --send, no email)
python3 scripts/etf_v7_threefactor.py 2>&1 | tail -20

# Dashboard gen smoke
python3 scripts/gen_dashboard.py 2>&1 | tail -10
```

## GitHub Issues
Use `gh issue create` to create issues for confirmed bugs.
Use `gh issue close <number>` when fixed.
Use `gh issue list` to see open issues.

## Commit Style
- fix: <short description>
- test: <short description>
- docs: <short description>

## Notes
- SQLite DB lives in ~/.etf-skill/workspace/etf_history.db (runtime)
- JSON outputs go to ~/.etf-skill/workspace/
- Repo static files: *.html, *.json at root level are the deployed frontend
