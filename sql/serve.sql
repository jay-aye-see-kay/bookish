-- ============================================================================
-- Server startup SQL (DuckDB). Run once at boot by cmd/serve.
-- Extends sql/similar.sql's book_meta / book_vecs patterns, but MATERIALIZES
-- the working sets into tables so each request avoids re-reading parquet and
-- re-scanning the 757k-row catalog window.
--
-- Assumes the process has already ATTACHed:
--   ATTACH 'data/books.duckdb' AS books (READ_ONLY);
--   ATTACH 'data/app.duckdb'   AS app;
-- Vectors are L2-normalized -> cosine == dot. See docs/dev/web-backend.md.
-- ============================================================================

-- one canonical row per embedded string (the most-reprinted edition wins)
CREATE OR REPLACE VIEW book_meta AS
SELECT 'Book|'||a.name||'|'||b.title AS input_text,
       b.work_key, a.name AS author, b.title,
       b.first_publish_year AS year, b.edition_count AS editions
FROM books.books b
JOIN books.authors a ON a.author_key = b.primary_author_key
QUALIFY row_number() OVER (
  PARTITION BY 'Book|'||a.name||'|'||b.title
  ORDER BY b.edition_count DESC, b.first_publish_year) = 1;

-- Materialized corpus: the ~27.5k embedded books with their vectors.
CREATE OR REPLACE TABLE corpus AS
SELECT m.input_text, m.work_key, m.author, m.title, m.year, m.editions, e.vec
FROM read_parquet('data/embeddings/*.parquet') e
JOIN book_meta m USING (input_text);

-- Materialized catalog: every work with an author + publish year, for search.
CREATE OR REPLACE TABLE catalog AS
SELECT b.work_key,
       a.name AS author,
       b.title,
       b.first_publish_year AS year,
       b.edition_count AS editions,
       'Book|'||a.name||'|'||b.title AS input_text,
       lower(a.name)  AS author_lc,
       lower(b.title) AS title_lc
FROM books.books b
JOIN books.authors a ON a.author_key = b.primary_author_key
WHERE b.first_publish_year IS NOT NULL
QUALIFY row_number() OVER (
  PARTITION BY b.work_key
  ORDER BY b.edition_count DESC, b.first_publish_year) = 1;

-- combined vector source: in-corpus books + on-demand embedded likes
CREATE OR REPLACE VIEW all_vecs AS
SELECT input_text, vec FROM corpus
UNION ALL
SELECT input_text, vec FROM app.ondemand_vecs;
