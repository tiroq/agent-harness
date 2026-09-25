from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

STATUSES = {"pending", "in_progress", "partial", "done", "blocked", "failed", "obsolete", "archived"}


def _template_root() -> Path:
    local = Path(__file__).resolve().parents[2] / "templates"
    if local.exists():
        return local
    return Path(sys.prefix) / "share" / "project-task-harness" / "templates"


def _field(path: Path, name: str) -> str:
    if not path.exists():
        return ""
    prefix = f"{name}:"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(prefix):
            value = line.split(":", 1)[1].strip().split("#", 1)[0].strip()
            return value.strip("\"'") if value not in {"null", "~"} else ""
    return ""


def _status(package: Path) -> str:
    value = _field(package / "task-status.yaml", "status").lower()
    return value if value in STATUSES else "pending"


def discover(root: Path) -> list[Path]:
    if (root / "task-status.yaml").is_file() or (root / "issues").is_dir():
        return [root]
    result: list[Path] = []
    for candidate in sorted(root.iterdir() if root.is_dir() else []):
        if not candidate.is_dir() or candidate.name in {"done", "archived", ".git", ".copilot-workflow"}:
            continue
        if (candidate / "task-status.yaml").is_file() or (candidate / "issues").is_dir() or (candidate / "spec.md").is_file():
            result.append(candidate)
    for bucket in ("todo", "done", "archived"):
        bucket_dir = root / bucket
        if bucket_dir.is_dir():
            result.extend(discover(bucket_dir))
    return list(dict.fromkeys(result))


def list_tasks(root: Path, short: bool = False) -> int:
    packages = discover(root)
    if short:
        for package in packages:
            if _status(package) not in {"done", "obsolete", "archived"}:
                print(package)
        return 0
    print(f"Tasks under {root}")
    print(f"{'PACKAGE':48} {'STATUS':12} {'CREATED':22} {'COMPLETED':22} {'RUN_ID':28}")
    print("-" * 118)
    for package in packages:
        status_file = package / "task-status.yaml"
        print(f"{str(package):48.48} {_status(package):12} "
              f"{_field(status_file, 'created_at') or '-':22.22} "
              f"{_field(status_file, 'completed_at') or '-':22.22} "
              f"{_field(status_file, 'last_run_id') or '-':28.28}")
    return 0


def _prompt(root: Path, source: str, run_id: str, model: str) -> str:
    template = _template_root() / "deliver-task.prompt.md"
    body = template.read_text(encoding="utf-8") if template.exists() else "Run the task through the full delivery lifecycle."
    return f"{body}\n\nREPO_ROOT: {root}\nRUN_ID: {run_id}\nARTIFACT_ROOT: .copilot-workflow/runs/{run_id}\nSOURCE: {source}\nMODEL: {model}\n"


def run_task(root: Path, source: str, model: str, dry_run: bool, allow_all: bool) -> int:
    packages = discover(root / source if source else root)
    if len(packages) == 1 and _status(packages[0]) in {"done", "obsolete", "archived"}:
        print(f"REFUSED: task package is non-executable: {packages[0]}", file=sys.stderr)
        return 4
    run_id = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    artifact = root / ".copilot-workflow" / "runs" / run_id
    artifact.mkdir(parents=True, exist_ok=True)
    for package in packages:
        status_file = package / "task-status.yaml"
        if status_file.exists() and _status(package) == "pending":
            lines = status_file.read_text(encoding="utf-8").splitlines()
            updated = []
            wrote_status = False
            wrote_run = False
            for line in lines:
                if line.lstrip().startswith("status:"):
                    updated.append("status: in_progress")
                    wrote_status = True
                elif line.lstrip().startswith("last_run_id:"):
                    updated.append(f'last_run_id: "{run_id}"')
                    wrote_run = True
                else:
                    updated.append(line)
            if not wrote_status:
                updated.insert(0, "status: in_progress")
            if not wrote_run:
                updated.append(f'last_run_id: "{run_id}"')
            status_file.write_text("\n".join(updated) + "\n", encoding="utf-8")
    prompt = _prompt(root, source or ".scratch", run_id, model)
    (artifact / "launch-request.txt").write_text(prompt, encoding="utf-8")
    args = ["copilot", "--agent=task-harness-orchestrator", "--model", model, "--autopilot",
            f"--max-autopilot-continues={os.getenv('COPILOT_MAX_AUTOPILOT_CONTINUES', '100')}"]
    if allow_all:
        args.append("--allow-all-tools")
    for denied in ("git push", "git merge", "git rebase", "git reset", "git clean", "git checkout", "git switch", "rm -rf", "sudo", "su", "chown", "chmod -R", "pip install", "uv pip install", "npm install"):
        args.append(f"--deny-tool=shell({denied})")
    print(f"RUN_ID: {run_id}\nARTIFACT_DIR: {artifact}\nMODEL: {model}\nSOURCE: {source or '.scratch'}")
    if dry_run:
        print("DRY_RUN: Copilot was not started.")
        print("ARGS:", " ".join(args))
        return 0
    try:
        return subprocess.run(args, cwd=root, input=prompt, text=True, check=False).returncode
    except FileNotFoundError:
        print("ERROR: copilot CLI is not installed or not on PATH", file=sys.stderr)
        return 127


def init_project(root: Path) -> int:
    for directory in (root / ".scratch" / "todo", root / ".scratch" / "done", root / ".copilot-workflow" / "runs"):
        directory.mkdir(parents=True, exist_ok=True)
    example = root / ".scratch" / "todo" / "example-feature"
    (example / "issues").mkdir(parents=True, exist_ok=True)
    seed_files = {
        example / "task-status.yaml": "status: pending\ncreated_at: \"" + dt.date.today().isoformat() + "\"\n",
        example / "spec.md": "# Feature\n\nDescribe the outcome and constraints.\n",
        example / "issues" / "001-example.md": "# T001 — Example task\n\n## Acceptance criteria\n\n- [ ] Replace this example with a real task.\n",
    }
    for target, content in seed_files.items():
        if not target.exists():
            target.write_text(content, encoding="utf-8")
    template_root = _template_root()
    prompt_source = template_root / "deliver-task.prompt.md"
    if prompt_source.exists():
        prompt_target = root / ".github" / "prompts" / "deliver-task.prompt.md"
        prompt_target.parent.mkdir(parents=True, exist_ok=True)
        if not prompt_target.exists():
            prompt_target.write_text(prompt_source.read_text(encoding="utf-8"), encoding="utf-8")
    agents_source = template_root / "agents"
    for source in sorted(agents_source.glob("*.agent.md")):
        target = root / ".github" / "agents" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    policy = root / "harness.yaml.example"
    policy_source = Path(__file__).resolve().parents[2] / "harness.yaml.example"
    if not policy_source.exists():
        policy_source = Path(sys.prefix) / "share" / "project-task-harness" / "harness.yaml.example"
    if policy_source.exists() and not policy.exists():
        policy.write_text(policy_source.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Initialized task harness directories in {root}")
    return 0


def queue_tasks(root: Path, tasks_dir: str, model: str, limit: int | None, select: bool, dry_run: bool, narrow_tools: bool) -> int:
    candidates = [p for p in discover(root / tasks_dir) if _status(p) not in {"done", "obsolete", "archived"}]
    if not candidates:
        print(f"No actionable tasks under {root / tasks_dir}")
        return 0
    if select:
        for index, package in enumerate(candidates, 1):
            print(f"{index}: {package}")
        selected = input("Select task number: ").strip()
        if not selected.isdigit() or not 1 <= int(selected) <= len(candidates):
            print("Invalid task selection", file=sys.stderr)
            return 2
        candidates = [candidates[int(selected) - 1]]
    elif limit is not None:
        candidates = candidates[:limit]
    for package in candidates:
        result = run_task(root, str(package), model, dry_run, not narrow_tools)
        if result:
            return result
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="task-harness")
    parser.add_argument("command", choices=("init", "list", "run", "queue"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--tasks-dir", default=".scratch")
    parser.add_argument("--source", default="")
    parser.add_argument("--model", default=os.getenv("COPILOT_MODEL", "gpt-5.3-codex"))
    parser.add_argument("--short", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--narrow-tools", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--select", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.command == "init":
        return init_project(root)
    if args.command == "list":
        return list_tasks(root / args.tasks_dir, args.short)
    if args.command == "queue":
        if args.limit is not None and args.limit <= 0:
            parser.error("--limit must be a positive integer")
        if args.select and args.limit is not None:
            parser.error("--select cannot be combined with --limit")
        return queue_tasks(root, args.tasks_dir, args.model, args.limit, args.select, args.dry_run, args.narrow_tools)
    return run_task(root, args.source or args.tasks_dir, args.model, args.dry_run, not args.narrow_tools)


if __name__ == "__main__":
    raise SystemExit(main())
