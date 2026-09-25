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

## Tool permissions

Default runner mode enables broad Copilot tools but denies destructive/history-changing commands, pushes, privilege escalation, recursive chmod, and ad-hoc dependency installation. Use `--narrow-tools` for a project with stricter policy. The deny-list is a safety boundary, not a replacement for code review.

## Project integration

The package intentionally does not copy BrandForge's quality gate or service-specific agents. Add a project-local `harness.yaml` (optional) with focused verification commands and read it from the project's orchestrator prompt. Keep credentials in environment/secret stores, never task files.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the lifecycle map and
[`docs/PORTING_FROM_BRANDFORGE.md`](docs/PORTING_FROM_BRANDFORGE.md) for the
migration boundary from the original project-specific setup.
