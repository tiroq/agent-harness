# Feature task package

- `spec.md` — feature scope and non-goals.
- `issues/` — one independently deliverable Markdown task per file.
- `task-status.yaml` — package lifecycle; new packages start as `pending`.
- `reports/` — task-local completion/audit reports (not workflow run logs).

Use explicit `Depends on: Txxx` links when ordering matters.
