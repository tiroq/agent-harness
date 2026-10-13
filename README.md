# Project Task Harness

Portable task packages and Copilot CLI orchestration for independent repositories. The harness deliberately keeps project code out of the package: a host repository owns its implementation, tests, quality commands, and task content; this package supplies the lifecycle, templates, status model, runner, and generic agent contract.

## Install

```bash
python -m pip install -e /path/to/project-task-harness
```

Then, in any project:

```bash
task-harness init --root .
task-harness list --root . --short
task-harness run --root . --tasks-dir .scratch/todo/my-feature --dry-run
task-harness queue --root . --tasks-dir .scratch/todo --limit 2
task-harness queue --root . --tasks-dir .scratch/todo --select
# Read-only GitHub Issue / existing PR / worktree preflight (no Copilot):
task-harness issue-preflight --root . --issue 1 --repo tiroq/agent-harness --expected-head <40-character-pr-sha> --dry-run
```

The runner passes the prompt to `copilot` on stdin. This avoids the common `Invalid command format` failure caused by treating an unquoted multiline prompt as separate CLI arguments.

## Task package contract

Every package is `.scratch/<feature-slug>/` and contains `task-status.yaml`, `INDEX.md`, `spec.md`, and `issues/*.md`. New packages use `status: pending`; the orchestrator changes it to `in_progress`, then `done` only after independent review, testing, evidence audit, and a verified local commit. Failed work is `blocked` or `failed` with a reason. `list --short` prints only executable packages, so completed work is not accidentally rerun.

## Generic delivery roles

The orchestrator contract expects five independent boundaries:

1. implementer — production change and focused tests, no commit;
2. reviewer — read-only architecture/regression/acceptance review;
3. test engineer — independent negative/boundary/recovery tests;
4. evidence auditor — reconciles reports against the actual diff;
5. committer — exact approved paths, one local commit, no push.

Projects can add provider-specific agents under `.github/agents/` without changing this package.

## Typed orchestration core

`task_harness.core` provides frontend-agnostic typed primitives for deterministic orchestration:

- role report intake and provenance validation (`ROLE`, `GENERATED_BY_ROLE`, `AGENT_SESSION_ID`);
- pre-audit mandatory-prerequisite gate decisions (skip auditor when a known-red mandatory gate already failed);
- baseline environment normalization and SHA-256 fingerprinting for reproducible baseline-vs-task evidence;
- explicit delivery outcome taxonomy (`BLOCKED_*`, `WAITING_EXTERNAL`, `FAILED_IMPLEMENTATION`, `REWORK`);
- per-role invocation tracking for usage-aware orchestration;
- repository/issue/branch/worktree identity + ownership classification primitives;
- typed lifecycle phase transitions including `REWORK` and blocked/failure outcome routing;
- runtime configuration precedence resolver (`request -> repository policy -> environment -> defaults`);
- authoritative issue packet materialization helpers with bounded latest-comment emphasis;
- typed parsing for refresh-helper exact-SHA evidence fields.

## GitHub Issue preflight (incremental Phase B/D)

The `issue-preflight` command is a **read-only** gate for the next
Issue-scoped delivery stage. Run it from the repository's **main checkout**,
with an existing `.worktree/issue-<number>` on the open PR's issue branch.
It validates the origin repository identity, clean local main equal to remote
main, registered clean issue worktree, issue-encoded branch ownership, exact
local/remote head, current integration ancestry, exactly one open PR on
`main`, and the live GitHub Issue body and paginated comments. It uses the
typed `IssueWorktree` and `materialize_issue_packet` primitives in memory.

A successful `issue-preflight` prints the Issue/PR/SHAs and latest
comment ID, but **never** launches Copilot, publishes/merges a PR, writes
artifacts or changes branch/worktree state. The `--dry-run` switch is
accepted for operator clarity; the command is always read-only. Nonzero
exit code 4 signals a fail-closed preflight blocker. It needs `git`, an
authenticated `gh` CLI and GitHub access.

This is NOT yet the full typed Issue execution engine, run/cancel controller,
MCP backend or publication/readiness state machine described by Issue #1.
Those remain future phases; a passing preflight must not be taken as
authorization to bypass exact-head review/CI or as proof of migration parity.

## Tool permissions

Default runner mode enables broad Copilot tools but denies destructive/history-changing commands, pushes, privilege escalation, recursive chmod, and ad-hoc dependency installation. Use `--narrow-tools` for a project with stricter policy. The deny-list is a safety boundary, not a replacement for code review.

## Project integration

The package intentionally does not copy BrandForge's quality gate or service-specific agents. Add a project-local `harness.yaml` (optional) with focused verification commands and read it from the project's orchestrator prompt. Keep credentials in environment/secret stores, never task files.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the lifecycle map.
