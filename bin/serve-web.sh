#!/usr/bin/env bash
# Run the bookish web frontend dev server.
set -euo pipefail
cd "$(dirname "$0")/../web"
exec npm run dev "$@"
