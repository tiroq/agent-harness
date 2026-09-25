#!/usr/bin/env bash
set -euo pipefail
ROOT="${ROOT:-$(git rev-parse --show-toplevel)}"
exec task-harness run --root "$ROOT" "$@"
