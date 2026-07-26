#!/usr/bin/env bash
#
# clean.sh
#
# Reads the big data/openlibrary.duckdb produced by extract.sh and builds a
# tidy, flat data/books.duckdb for the recommendation app: fiction works with
# 2+ editions, no raw JSON, split into books / authors / book_authors.
#
# No params. Both files live in data/.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/data"

SRC="$DATA/openlibrary.duckdb"
OUT="$DATA/books.duckdb"

if [ ! -f "$SRC" ]; then
  echo "error: $SRC not found -- run bin/extract.sh <dump> first" >&2
  exit 1
fi

echo "Cleaning $SRC"
echo "      -> $OUT"
rm -f "$OUT"

# Notes on the transforms:
#   fiction       = the word "fiction" appears in subjects on the work OR any edition
#   edition_count = popularity proxy (number of editions of the work)
#   first_publish_year = work's own field, else earliest edition publish year (~100% coverage)
#   description   = OL stores it as {type,value} or a plain string; unwrap both,
#                   fall back to an edition description
SQL=$(cat <<'EOSQL'
ATTACH '@OUT@' AS b;

-- Per-work edition aggregates (single pass over all editions)
CREATE TEMP TABLE ed_agg AS
SELECT work_key,
  count(*) AS edition_count,
  min(TRY_CAST(regexp_extract(json_extract_string(json,'publish_date'),'\d{4}') AS INT)) AS min_year,
  max(CASE WHEN lower(CAST(json_extract(json,'subjects') AS VARCHAR)) LIKE '%fiction%' THEN 1 ELSE 0 END) AS ed_fiction,
  max(json_extract_string(json,'$.isbn_13[0]')) AS isbn13,
  max(CASE WHEN json_type(json_extract(json,'description'))='OBJECT'
           THEN json_extract_string(json,'$.description.value')
           ELSE json_extract_string(json,'description') END) AS ed_desc
FROM editions WHERE work_key IS NOT NULL
GROUP BY work_key;

-- Selected works: fiction + >=2 editions
CREATE TEMP TABLE sel AS
SELECT w.key AS work_key, w.json, e.edition_count, e.min_year, e.ed_fiction, e.isbn13, e.ed_desc
FROM works w JOIN ed_agg e ON e.work_key = w.key
WHERE e.edition_count >= 2
  AND (lower(CAST(json_extract(w.json,'subjects') AS VARCHAR)) LIKE '%fiction%' OR e.ed_fiction = 1);

-- books (one row per work, no JSON)
CREATE TABLE b.books AS
SELECT
  work_key,
  replace(work_key,'/works/','') AS ol_id,
  json_extract_string(json,'title') AS title,
  json_extract_string(json,'subtitle') AS subtitle,
  coalesce(TRY_CAST(regexp_extract(json_extract_string(json,'first_publish_date'),'\d{4}') AS INT), min_year) AS first_publish_year,
  edition_count,
  coalesce(
    CASE WHEN json_type(json_extract(json,'description'))='OBJECT'
         THEN json_extract_string(json,'$.description.value')
         ELSE json_extract_string(json,'description') END,
    ed_desc) AS description,
  TRY_CAST(json_extract(json,'subjects') AS VARCHAR[]) AS subjects,
  json_extract_string(json,'$.authors[0].author.key') AS primary_author_key,
  TRY_CAST(json_extract_string(json,'$.covers[0]') AS INT) AS cover_id,
  isbn13
FROM sel;

-- book_authors junction (works can have several authors)
CREATE TABLE b.book_authors AS
SELECT s.work_key,
       json_extract_string(a, '$.author.key') AS author_key,
       pos AS position
FROM sel s, unnest(TRY_CAST(json_extract(s.json,'authors') AS JSON[])) WITH ORDINALITY t(a, pos)
WHERE json_extract_string(a, '$.author.key') IS NOT NULL;

-- authors (only those referenced by selected books)
CREATE TABLE b.authors AS
SELECT key AS author_key,
       json_extract_string(json,'name') AS name,
       json_extract_string(json,'birth_date') AS birth_date,
       json_extract_string(json,'death_date') AS death_date,
       CASE WHEN json_type(json_extract(json,'bio'))='OBJECT'
            THEN json_extract_string(json,'$.bio.value')
            ELSE json_extract_string(json,'bio') END AS bio,
       TRY_CAST(json_extract(json,'alternate_names') AS VARCHAR[]) AS alternate_names
FROM authors
WHERE key IN (SELECT DISTINCT author_key FROM b.book_authors);

CHECKPOINT b;
EOSQL
)

echo "${SQL//@OUT@/$OUT}" | duckdb "$SRC"

echo "Done. Tables:"
duckdb "$OUT" -box "SELECT 'books' t, count(*) n FROM books UNION ALL SELECT 'authors', count(*) FROM authors UNION ALL SELECT 'book_authors', count(*) FROM book_authors;"
