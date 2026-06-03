# Ralph BUILDING Loop — ETF Watcher Bug Fix

## Goal
Fix all frontend, backend, and data layer bugs in this A股 ETF 国家队三因子监控看板.

## Repo
https://github.com/Jeremygarden/ashare-etf-watcher-dash (already cloned here)

## Context Files
- specs/bugs.md — detailed bug list with acceptance criteria
- IMPLEMENTATION_PLAN.md — task tracking (create/update each iteration)
- AGENTS.md — backpressure test commands

## Your Job Each Iteration
1. Read specs/bugs.md and current IMPLEMENTATION_PLAN.md
2. Pick the highest-priority unfixed bug
3. Investigate the code carefully
4. Fix it
5. Run backpressure tests (see AGENTS.md)
6. Update IMPLEMENTATION_PLAN.md (mark task done, add notes)
7. Commit with a clear message like: "fix: <description>"

## Rules
- Work autonomously. Do NOT ask for confirmation between iterations.
- Do not skip running tests after each fix.
- Prefer targeted fixes — don't refactor code that isn't broken.
- If a bug is not reproducible, document why and mark it investigated.
- Use GitHub Issues via `gh` CLI to track bugs found: create an issue for each confirmed bug, close it when fixed.

## Completion
When all bugs in specs/bugs.md are addressed (fixed or documented as non-issue), add this line to IMPLEMENTATION_PLAN.md:

STATUS: COMPLETE
