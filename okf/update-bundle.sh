#!/usr/bin/env bash
set -euo pipefail

OKF_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$OKF_DIR")"

"$OKF_DIR/build-site.sh"
mkdir -p "$REPO_ROOT/knowledge/dist"
cp "$REPO_ROOT/public/static/okf-graph.json" \
  "$REPO_ROOT/knowledge/dist/okf-graph.json"
