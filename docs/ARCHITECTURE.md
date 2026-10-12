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

## Typed core boundary

Deterministic orchestration semantics live in `task_harness.core` so CLI and
future MCP adapters can share one implementation boundary:

- role-report provenance validation at intake;
- mandatory pre-audit prerequisite gate decisions;
- normalized baseline environment fingerprints for task-vs-baseline evidence;
- typed blocked vs failure outcome taxonomy;
- role invocation usage tracking.

## Filesystem contract

- `.scratch/` contains task packages; optional `todo/` and `done/` buckets are
  organizational only.
- `.copilot-workflow/runs/` contains launch prompts and role reports and is
  ignored by Git.
- `task-status.yaml` is the lifecycle source of truth. A checklist or filename
  cannot prove completion.
