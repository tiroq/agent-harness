#!/usr/bin/env bash
# Repository-owned mandatory local evidence gate for agent-harness itself.
# Use the Python environment chosen by the operator; never install deps.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m compileall -q src
PYTHONPATH=src python3 -m pytest -q tests
git diff --check
