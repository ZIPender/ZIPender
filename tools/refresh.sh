#!/usr/bin/env bash
# Fetch fresh numbers, re-render everything, and refresh the preview pages in .preview/.
#   tools/refresh.sh            # fetch + render (token from $PROFILE_TOKEN, else git's GitHub credential)
#   tools/refresh.sh --offline  # render from data/stats.json only
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -x .venv/bin/python ]] || { uv venv -q .venv && uv pip install -q --python .venv/bin/python fonttools brotli pillow; }
if [[ ${1:-} != --offline && -z ${PROFILE_TOKEN:-} ]]; then
  PROFILE_TOKEN=$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill 2>/dev/null | sed -n 's/^password=//p') || true
  export PROFILE_TOKEN
fi
.venv/bin/python src/build.py "$@"
tools/preview.sh
