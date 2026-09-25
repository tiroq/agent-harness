# Harness architecture

```text
task package (.scratch/<feature>/)
        |
        v
CLI (list / run / queue) ----> .copilot-workflow/runs/<run-id>/
        |
        v
Copilot CLI (prompt on stdin, autopilot, deny-list)
        |
        v
orchestrator
  implementer -> reviewer -> test-engineer -> rework loop
       -> evidence auditor -> committer -> post-commit auditor
        |
        v
task-status.yaml (pending -> in_progress -> done/blocked/failed)
```

The package owns lifecycle policy and reusable roles. The host project owns
source code, tests, secrets, quality commands, deployment, and domain agents.

## Filesystem contract

- `.scratch/` contains task packages; optional `todo/` and `done/` buckets are
  organizational only.
- `.copilot-workflow/runs/` contains launch prompts and role reports and is
  ignored by Git.
- `task-status.yaml` is the lifecycle source of truth. A checklist or filename
  cannot prove completion.
