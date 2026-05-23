#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Simple, reliable run script:
# - create venv if missing
# - activate (for interactive shells)
# - install requirements when requirements.txt changes (using sha256sum)
# - run uvicorn with the venv python

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# activate for interactive shells
# shellcheck disable=SC1091
source .venv/bin/activate

REQ_HASH_FILE=".venv/.reqs_hash"
current_hash=$(sha256sum requirements.txt | cut -d' ' -f1)
if [ ! -f "$REQ_HASH_FILE" ] || [ "$current_hash" != "$(cat $REQ_HASH_FILE)" ]; then
  .venv/bin/python -m pip install -r requirements.txt
  echo "$current_hash" > "$REQ_HASH_FILE"
fi

exec .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
