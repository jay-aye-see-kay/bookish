-- ============================================================================
-- Book-similarity exploration toolkit (DuckDB).
-- Load with:  duckdb -init sql/similar.sql
--        or:  .read sql/similar.sql   (inside a duckdb session)
-- Vectors are L2-normalized -> cosine == dot product, range [-1,1].
-- See docs/dev/comparing-books.md
-- ============================================================================

ATTACH IF NOT EXISTS 'data/books.duckdb' AS books (READ_ONLY);

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

-- embeddings joined to metadata (the working table for everything)
CREATE OR REPLACE VIEW book_vecs AS
SELECT m.input_text, m.work_key, m.author, m.title, m.year, m.editions, e.vec
FROM 'data/embeddings/*.parquet' e
JOIN book_meta m USING (input_text);

-- resolve a fuzzy query to candidate rows (most editions first)
CREATE OR REPLACE MACRO find_book(q) AS TABLE
  SELECT input_text, author, title, year, editions
  FROM book_vecs
  WHERE title ILIKE '%'||q||'%' OR input_text ILIKE '%'||q||'%'
  ORDER BY editions DESC;

-- nearest neighbours to the best match of q.
--   same_author := false  ->  drop same-author books (theme-first view)
CREATE OR REPLACE MACRO similar_to(q, n := 10, same_author := true) AS TABLE
  WITH qb AS (
    SELECT input_text, vec, author FROM book_vecs
    WHERE title ILIKE '%'||q||'%' OR input_text ILIKE '%'||q||'%'
    ORDER BY editions DESC LIMIT 1)
  SELECT bv.author, bv.title, bv.year, bv.editions,
         round(list_cosine_similarity(bv.vec, qb.vec), 3) AS sim
  FROM book_vecs bv, qb
  WHERE bv.input_text <> qb.input_text
    AND (same_author OR bv.author <> qb.author)
  ORDER BY sim DESC LIMIT n;

-- pairwise similarity between the best match of two queries
CREATE OR REPLACE MACRO similar_between(qa, qb) AS TABLE
  WITH a AS (SELECT vec, title FROM book_vecs
             WHERE title ILIKE '%'||qa||'%' ORDER BY editions DESC LIMIT 1),
       b AS (SELECT vec, title FROM book_vecs
             WHERE title ILIKE '%'||qb||'%' ORDER BY editions DESC LIMIT 1)
  SELECT a.title AS book_a, b.title AS book_b,
         round(list_cosine_similarity(a.vec, b.vec), 3) AS sim
  FROM a, b;

-- "because you liked these" — mean cosine to a set of input_text strings.
-- (ranking by mean cosine == ranking by cosine-to-centroid, simpler SQL)
CREATE OR REPLACE MACRO recommend(liked_texts, n := 10) AS TABLE
  WITH liked AS (SELECT vec FROM book_vecs
                 WHERE input_text IN (SELECT unnest(liked_texts)))
  SELECT bv.author, bv.title, bv.year,
         round(avg(list_cosine_similarity(bv.vec, liked.vec)), 3) AS sim
  FROM book_vecs bv CROSS JOIN liked
  WHERE bv.input_text NOT IN (SELECT unnest(liked_texts))
  GROUP BY bv.author, bv.title, bv.year
  ORDER BY sim DESC LIMIT n;
