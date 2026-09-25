---
mode: agent
agent: task-harness-orchestrator
description: Deliver one portable task package through implementation, review, testing, evidence audit, and a verified local commit.
---

# Portable task delivery

Treat the supplied task package as authoritative. Never stop after implementation. Run:

`implement → focused tests → simplification review → independent code review → independent tests → rework loop → evidence audit → one local commit → post-commit audit`

Use `.scratch/<feature>/task-status.yaml` as the lifecycle source of truth. Refuse packages with `done`, `obsolete`, or `archived` status unless explicitly overridden. Keep reports under `.copilot-workflow/runs/<run-id>/`; never stage them.

For every UI control prove: rendered control → request URL/method/inputs → backend handler → state/job effect → user-visible feedback → regression test.

Do not push, merge, rebase, reset, clean, checkout-overwrite, install dependencies, or handle secrets. If a required dependency is missing, report the command and stop as blocked. A terse “implemented” message is not completion.
