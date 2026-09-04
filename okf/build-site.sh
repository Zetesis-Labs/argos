#!/usr/bin/env bash
# El toolkit fijado posee la receta; este checkout aporta corpus y perfil.
set -euo pipefail

OKF_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$OKF_DIR")"
CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/argos-okf"
TOOLKIT_REF="$(cat "$OKF_DIR/quartz-okf.ref")"
TOOLKIT="$CACHE_ROOT/toolkit-${TOOLKIT_REF}"

if [ ! -f "$TOOLKIT/package.json" ]; then
  mkdir -p "$TOOLKIT"
  curl -fsSL "https://github.com/Zetesis-Labs/quartz-okf/archive/${TOOLKIT_REF}.tar.gz" |
    tar xz --strip-components=1 -C "$TOOLKIT"
fi

exec node "$TOOLKIT/core/bin/okf-build.js" "$REPO_ROOT" --cache "$CACHE_ROOT" "$@"
