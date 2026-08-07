#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
: "${PERSONAL_DIET_PANTRY_DATA_DIR:?Set PERSONAL_DIET_PANTRY_DATA_DIR to an existing isolated data directory}"
DATA_DIR="$PERSONAL_DIET_PANTRY_DATA_DIR"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -f "$DATA_DIR/diet.sqlite" ]]; then
  printf 'No database found at %s\n' "$DATA_DIR/diet.sqlite" >&2
  exit 2
fi

if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="$PACKAGE_ROOT/python:$PYTHONPATH"
else
  export PYTHONPATH="$PACKAGE_ROOT/python"
fi
export PERSONAL_DIET_PANTRY_DATA_DIR="$DATA_DIR"

"$PYTHON_BIN" -m personal_diet_pantry.maintenance backup
