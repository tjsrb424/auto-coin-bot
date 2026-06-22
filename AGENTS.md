# Project Working Rules

## Codex Handoff

- Before continuing multi-market automation, live trading, scheduler/orchestrator, capital allocator, or production diagnosis work, read `docs/CODEX_HANDOFF.md` first.

## Commit And Push Routine

- Before every commit and push, keep the left-bottom sidebar Build label enabled.
- The Build label must show the package version and git short commit hash, for example `v0.0.1 · 567bc60`.
- Do not replace the Build label with a hardcoded manual value. It should continue to come from the Vite build constants in `vite.config.ts`.
- If Docker or server deployment shows `unknown`, fix the build hash pipeline before considering the deployment complete.
- Run `npm run build` before committing frontend changes.
- For backend Python changes, run `py_compile` on the touched backend modules before committing.
