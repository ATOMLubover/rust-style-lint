#!/usr/bin/env sh
set -eu

export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"

UV_SYNC=false
if [ ! -f .venv/bin/python ]; then
    echo "→ creating venv …"
    uv venv .venv --seed 2>/dev/null
    UV_SYNC=true
fi

if $UV_SYNC || [ ! -f .venv/.sync-stamp ]; then
    echo "→ installing dependencies …"
    uv pip sync requirements.txt --python .venv/bin/python
    date +%s > .venv/.sync-stamp
fi

exec .venv/bin/python -m rust_style_lint "$@"
