#!/usr/bin/env bash
# Run the bookish JSON backend (embedded DuckDB + HTTP API).
# The llama-server embedding backend is started separately, OUTSIDE the
# sandbox, via bin/serve-embed.sh. Recommendations work offline for likes
# already in the 27.5k corpus; out-of-corpus likes need that server.
#
# Env overrides: BOOKISH_ADDR, BOOKISH_BOOKS_DB, BOOKISH_APP_DB,
#                BOOKISH_SERVE_SQL, BOOKISH_EMBED_URL
set -euo pipefail
cd "$(dirname "$0")/.."
export CGO_ENABLED=1
exec go run ./cmd/serve "$@"
