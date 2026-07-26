#!/usr/bin/env bash
#
# extract.sh <ol_dump_file.txt.gz>
#
# Reads the Open Library "all types" dump (gzipped TSV, kept in data/) directly
# with DuckDB -- no decompression to disk -- and materialises one big database
# data/openlibrary.duckdb with three typed tables (authors, works, editions),
# each keeping the full raw JSON so anything can be re-derived later.
#
# The dump file must live in data/. Pass only its name, e.g.:
#   bin/extract.sh ol_dump_2026-06-30.txt.gz
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/data"

if [ "$#" -ne 1 ]; then
  echo "usage: bin/extract.sh <ol_dump_file.txt.gz>   (file must live in data/)" >&2
  exit 1
fi

DUMP="$DATA/$1"
OUT="$DATA/openlibrary.duckdb"

if [ ! -f "$DUMP" ]; then
  echo "error: dump not found: $DUMP" >&2
  exit 1
fi

echo "Extracting $DUMP"
echo "        -> $OUT"
rm -f "$OUT"

# The dump is a 5-column TSV: type \t key \t revision \t timestamp \t JSON.
#   quote=''            : do not treat the quotes inside the JSON as CSV quoting
#   max_line_size=40MB  : a few editions have giant table_of_contents blobs
SQL=$(cat <<'EOSQL'
CREATE TABLE authors AS
SELECT column1 AS key,
       json_extract_string(column4,'name') AS name,
       column4::JSON AS json
FROM read_csv('@DUMP@', delim='\t', quote='', header=false, max_line_size=40000000,
     columns={'column0':'VARCHAR','column1':'VARCHAR','column2':'VARCHAR','column3':'VARCHAR','column4':'VARCHAR'})
WHERE column0 = '/type/author';
CHECKPOINT;

CREATE TABLE works AS
SELECT column1 AS key,
       json_extract_string(column4,'title') AS title,
       column4::JSON AS json
FROM read_csv('@DUMP@', delim='\t', quote='', header=false, max_line_size=40000000,
     columns={'column0':'VARCHAR','column1':'VARCHAR','column2':'VARCHAR','column3':'VARCHAR','column4':'VARCHAR'})
WHERE column0 = '/type/work';
CHECKPOINT;

CREATE TABLE editions AS
SELECT column1 AS key,
       json_extract_string(column4,'title') AS title,
       json_extract_string(column4,'$.works[0].key') AS work_key,
       json_extract_string(column4,'$.authors[0].key') AS author_key,
       column4::JSON AS json
FROM read_csv('@DUMP@', delim='\t', quote='', header=false, max_line_size=40000000,
     columns={'column0':'VARCHAR','column1':'VARCHAR','column2':'VARCHAR','column3':'VARCHAR','column4':'VARCHAR'})
WHERE column0 = '/type/edition';
CHECKPOINT;
EOSQL
)

echo "${SQL//@DUMP@/$DUMP}" | duckdb "$OUT"

echo "Done. Tables:"
duckdb "$OUT" -box "SELECT 'authors' t, count(*) n FROM authors UNION ALL SELECT 'works', count(*) FROM works UNION ALL SELECT 'editions', count(*) FROM editions;"
