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

## GitHub Issue session execution (bounded Phase B)

A successful `issue-preflight` can now be followed by a **manually
authorized** Issue Copilot session in the existing repository worktree.
The defaults are deliberately safe and do not start Copilot:

```bash
# From the agent-harness main checkout; existing Issue 1 worktree/PR 2 only:
task-harness issue-run --root . --issue 1 --repo tiroq/agent-harness \
  --expected-head <current-40-character-PR-head>

# Explicit human launch (foreground; this actually spends Copilot requests):
task-harness issue-run --root . --issue 1 --repo tiroq/agent-harness \
  --expected-head <current-40-character-PR-head> --execute

# While running, from a second terminal:
task-harness issue-status --root . --issue 1
task-harness issue-cancel --root . --issue 1
```

## Explicit session resume and recovery

After an Issue-scoped session exits, the ledger retains a **Copilot
session UUID** and an immutable per-attempt run log. New `issue-run --execute`
is blocked when any prior run record exists; continuing that Issue requires
an explicit `issue-resume` command, using the **same** `--session-id` rather
than `--continue` (which could select another repository's session).

```bash
# Read-only resume plan; never starts or charges Copilot:
task-harness issue-resume --root . --issue 1 --repo tiroq/agent-harness \
  --expected-head <existing-open-PR-head>

# Observe possible orphaned/stale process ownership:
task-harness issue-status --root . --issue 1

# Explicitly reconcile an interrupted run after verifying its process group:
task-harness issue-reconcile --root . --issue 1

# Only when recovery reports INTERRUPTED_CONFIRMED, or the old attempt is
# SESSION_EXITED / FAILED_SESSION / CANCELLED, and the fresh dry-run passes:
task-harness issue-resume --root . --issue 1 --repo tiroq/agent-harness \
  --expected-head <existing-open-PR-head> --execute
```

Reconciliation is **fail-closed**: if a process with the original
PID/starttime is alive, its group may still exist, or the recorded PID/group
cannot be verified, no automatic retry is performed. A confirmed host
reboot or absence of the recorded process group transitions a stranded
RUNNING/CANCEL_REQUESTED record to `INTERRUPTED`; `STARTING` without a
recorded child PID remains blocked for manual review, because a child
could have started before the launcher crashed. This also refuses old
run records without a known session UUID; it never guesses a session.

Resume accepts uncommitted changes left in the **same** worktree but still
requires exactly the previously recorded repo, Issue, PR, remote branch
HEAD, local HEAD, and current integration boundary. A prior local commit
that moves HEAD away from the open PR branch remains blocked pending
explicit reconciliation/publication. Session exit alone still does not
satisfy independent review or PR-readiness gates.

The implementation uses Copilot CLI's `--session-id=<UUID>` flag. Verify
that the installed Copilot CLI version supports this option *before a
paid `--execute` run*. No real paid-session recovery has yet been
dogfooded; hermetic fake-Copilot process tests are the first gate.

The new backend calls the existing live GitHub Issue preflight and **shares the
same Copilot tool deny-list** used by portable task-package sessions. It
creates a private run directory with the authoritative Issue/comments prompt
and a bounded (~2 MiB) transcript, plus a per-repo, per-Issue locked status
ledger. Concurrent launches of the same Issue are rejected even when the
previous owner's PID is missing/uncertain. Cancellation only signals a child
whose Linux PID start time and boot ID match the recorded identity; it does
not kill an unverified or reused process.

`SESSION_EXITED` only means the Copilot process returned exit code 0.
It is **not** PR-ready, approval, verified commit, or Issue completion.
Live GitHub checks, independent role provenance/review/audit, session resume,
typed end-to-end delivery phases, artifact publication and MCP/controller
parity remain unimplemented. If the launching parent is killed before it
records exit status, the state remains conservatively active/unknown; it
must not be automatically restarted or marked successful. This bounded
session executor is Linux-only for safe cancellation (`/proc` identity).

## Deterministic independent-role delivery lifecycle (Phase B/C foundation)

The new reusable `task_harness.core.delivery_pipeline` and
`task_harness.core.role_orchestration` modules implement the bounded
**implementer → independent reviewer → test engineer → evidence auditor →
committer → independent post-commit auditor** flow. This is a typed engine
over an injected `RoleTransport` and trusted `GitEvidence` adapter rather
than another BrandForge-specific shell state machine.

The core generates a fresh role ticket and session identity for each
independent invocation; accepts reports through the existing typed role
provenance validator; rejects replay, stale run/diff/HEAD, or changes from
read-only reviewer/auditor roles. A test engineer changing tests triggers
a fresh review; a test engineer modifying production source is rejected
against locally observed per-path content digests. Before the evidence
auditor is invoked, mandatory checks must be explicitly PASS (UNKNOWN
is not PASS). The auditor approves unique repository-relative paths and
the exact diff; the committer must produce a child of the approved HEAD,
commit **exactly** the approved files, and leave the worktree clean. The
post-commit auditor verifies the same commit and clean boundary.

The terminal core state is **`LOCAL_AUDITED`**, deliberately NOT GitHub
PR_READY/DONE. `drive_pipeline` has a hard role-count cap and can be
exercised with a hermetic mock role transport; `LocalGitEvidence` uses
read-only Git commands, including binary tracked/untracked content digests,
to supply independent evidence rather than accepting model claims.

**Current integration boundary:** the generic role-transport interface
and deterministic driver are implemented, but the paid Copilot subprocess
for *each independent role* is not yet connected to the existing persistent
Issue session/owner ledger. Thus this is not a production unattended
role engine or a replacement for BrandForge's full review/publication
workflow. The existing `issue-run --execute` remains a bounded
orchestrator-session launcher; it does not yet automatically enforce this
role engine. Do not conflate them until exact-head end-to-end dogfood passes.

### Real process group crash/cancel tests

`tests/test_process_crash_integration.py` starts the actual Linux
`issue_execution.issue_run` launcher with an isolated fake `copilot`
executable, then SIGKILLs the launcher while its fake child is alive.
Recovery must refuse to call it INTERRUPTED while the child/group still
exists; a separate test sends an owner-verified cancellation and requires
a durable CANCELLED exit. No premium requests or network access are used.
These tests require Linux `/proc` and should be run on tsrv before
authorizing a real paid session.

## Copilot internal subagent checkpoints (one parent session)

**One** `task-harness issue-run --execute` starts **one** top-level Copilot CLI
process/session with a repository-local `task-harness-orchestrator`. The
orchestrator delegates implementation/review/test/audit to *internal*
Copilot agents with `task`, `read_agent`, and `write_agent`. Agent Harness
does NOT start a separate top-level `copilot` process per role.

The parent launch now exposes `HARNESS_ROOT`, `ISSUE_NUMBER`,
`HARNESS_RUN_ID`, `HARNESS_PARENT_SESSION_ID`, and `ARTIFACT_ROOT` to its
orchestrator. The checkpoint commands operate inside the **existing** per-Issue
locked run ledger (no duplicate process/role supervisor or database):

```bash
# Orchestrator only: take snapshot BEFORE running the internal Copilot task.
# Synchronous Copilot task may return subagent ID only after completion.
python3 -m task_harness.cli issue-role-begin \
  --root "$HARNESS_ROOT" --issue "$ISSUE_NUMBER" \
  --run-id "$HARNESS_RUN_ID" \
  --parent-session-id "$HARNESS_PARENT_SESSION_ID"

# Invoke Copilot's INTERNAL task/read_agent tools; DO NOT launch another
# top-level Copilot process. When the task returns an actual subagent handle,
# save the report as $ARTIFACT_ROOT/reviewer.md with ROLE,
# GENERATED_BY_ROLE, AGENT_SESSION_ID and VERDICT.
python3 -m task_harness.cli issue-role-complete \
  --root "$HARNESS_ROOT" --issue "$ISSUE_NUMBER" \
  --run-id "$HARNESS_RUN_ID" \
  --parent-session-id "$HARNESS_PARENT_SESSION_ID" \
  --subagent-id "<actual task tool handle>" --report reviewer.md

# Read-only diagnostic from a second terminal:
python3 -m task_harness.cli issue-role-status --root . --issue 1
```

`issue-role-begin` requires a live exact parent PID/session, the correct
Issue-owned worktree, and a clean phase transition in the existing typed
delivery pipeline. It can reserve an internal ticket BEFORE the actual
subagent handle is known; `issue-role-complete --subagent-id` binds the
handle returned by a synchronous Copilot `task` and verifies it is unused. It captures a
trusted local Git snapshot **before** the delegated subagent works.
`issue-role-complete` takes only a bounded, non-symlink .md file in
the active parent's artifact directory, rejects duplicate/missing/mismatched
role provenance, compares the current Git HEAD/diff/files to the saved
snapshot, and records the accepted phase in that SAME parent's ledger.
The checkpoint survives `issue-resume` of the parent process. A stranded
pending checkpoint remains blocked rather than being silently promoted
or dropped.

**Trust boundary:** the orchestrator's asserted internal agent handle is
`ORCHESTRATOR_ASSERTED_UNVERIFIED`. The new project Copilot CLI hooks
(`.github/hooks/task-harness.json`) also observe `subagentStart` and
`subagentStop`. The latter carries `agentId`, `agentName`, `sessionId`
and the full final response. The recorder retains only bounded metadata
and a response hash, not the full (potentially sensitive) output.
`issue-role-complete` rejects contradictions between a recorded hook
agent ID, a submitted role report and the pending ticket; a matching event
is labeled `COPILOT_HOOK_OBSERVED_UNATTESTED`. Hooks are runtime
observations, **not cryptographic proof**: Copilot may disable hooks, hook
handlers can fail open, and code in the same worktree/process may spoof a
payload. A missing event leaves the lower-trust asserted-only verdict.
Neither level is remote PR-ready/merge authority. If Copilot supplies no
real subagent handle, do not invent one. Pre-change snapshots must precede
the internal `task` invocation.

Before launching the **pre-commit evidence auditor**, the parent orchestrator
must run the fixed committed repository gate:

```bash
python3 -m task_harness.cli issue-quality-check \
  --root "$HARNESS_ROOT" --issue "$ISSUE_NUMBER" \
  --run-id "$HARNESS_RUN_ID" \
  --parent-session-id "$HARNESS_PARENT_SESSION_ID"
```

This executes only the unchanged committed
`scripts/quality-gate.sh` with a bounded timeout, sanitized internal
one-hop environment flags, and a Git HEAD/diff snapshot. The command
cannot take an arbitrary shell command from the model, and cannot forge a
PASS when the script is missing, altered, times out, returns nonzero, or
mutates tracked/untracked Git evidence. Both `issue-role-begin` and
`issue-role-complete` recheck exact HEAD/diff provenance. This is a
**local committed-script quality check**, NOT exact-head GitHub Actions
or required-check readiness. A target repo must explicitly have a trusted
`scripts/quality-gate.sh` committed before the pre-audit role is possible.
No weak fallback that treats an absent check as PASS is supported.

An interrupted role checkpoint is preserved across parent
`issue-resume`. In a **new** active parent run with the same Copilot UUID,
`issue-role-reconcile --root "$HARNESS_ROOT" --issue "$ISSUE_NUMBER"
--run-id "$HARNESS_RUN_ID" --parent-session-id "$HARNESS_PARENT_SESSION_ID"`
can permit the same role to be retried only when the previous-run ancestry
matches and the complete Git snapshot is unchanged. An old role that changed
HEAD/diff/files remains BLOCKED pending manual evidence recovery rather than
being silently reset. It never launches another Copilot CLI process.

Installable project templates now include the optional, guarded Copilot
hooks under `templates/hooks`; `task-harness init` copies them without
overwriting existing project files. Hook event handling remains inert if
`HARNESS_RUN_ID` is absent. See the [official Copilot hooks reference]
(https://docs.github.com/en/copilot/reference/hooks-reference) for the
`subagentStart/subagentStop` payloads.

The earlier `RoleTransport`/`drive_pipeline` remains a standalone
**hermetic test abstraction**, not a production instruction to launch
multiple Copilot subprocesses. The production source of internal role
delegation remains the one Copilot orchestrator.

## Controlled real Copilot subagent hook probe

To test delivery of `subagentStart` and `subagentStop` from the **installed
Copilot CLI** without mutating an Issue/worktree, use the isolated operator
probe. This is a **separate** diagnostic from `issue-run --execute`; it does
not run the Issue #1 delivery pipeline or attest role-to-PR readiness.

```bash
# No model/API call: validates fixture setup and prints dry-run metadata.
python3 scripts/probe-copilot-subagent-hooks.py

# ONE isolated Copilot CLI invocation with one requested internal custom
# subagent and only the task tool available. This MAY consume AI credits:
python3 scripts/probe-copilot-subagent-hooks.py --live --timeout-sec 120
```

The probe creates a throwaway Git repository with a custom
`hook-probe-coordinator` and `hook-probe-worker`, registers two project hook
commands, opts in to repo hooks in noninteractive `-p` mode via
`GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS=true`, and asks the coordinator to
delegate once. It denies shell/write/URL tools and builtin MCP, limits the
available tool set to `task`, uses a soft per-response
`--max-ai-credits=2` limit and an outer process-group timeout. This is
not an absolute spend ceiling: invoking the parent and worker can consume
more than one model call. Neither BrandForge nor agent-harness Issue
worktrees/ledgers/PRs are modified.

The summary is PASS only if the CLI exited successfully and exactly one
matching custom worker `subagentStart` and `subagentStop` were observed
with the same parent session ID, matching isolated cwd, nonempty
`subagentStop.agentId` and `stopReason=end_turn`. Only short hashes of
IDs are retained in the temporary event log; the potentially sensitive
raw final worker response is never written to that log. The temporary
repository and CLI output are deleted after the run unless `--keep` is
requested; `--keep` stores private diagnostics under a printed `/tmp`
path and requires manual cleanup.

If the probe returns FAIL, report its JSON verdict and diagnostic tail.
Do not start the real Issue executor or call its `--execute` on the
basis of a failed smoke test. Note that a successful isolated hook probe
proves CLI delivery in that environment only; the production ledger
association, independent auditor gates and non-disableable event provenance
still need a separately authorized one-parent-session end-to-end run.

## Tool permissions

Default runner mode enables broad Copilot tools but denies destructive/history-changing commands, pushes, privilege escalation, recursive chmod, and ad-hoc dependency installation. Use `--narrow-tools` for a project with stricter policy. The deny-list is a safety boundary, not a replacement for code review.

## Project integration

The package intentionally does not copy BrandForge's quality gate or service-specific agents. Add a project-local `harness.yaml` (optional) with focused verification commands and read it from the project's orchestrator prompt. Keep credentials in environment/secret stores, never task files.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the lifecycle map.
