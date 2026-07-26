## Comparing books — exploration toolkit

Exploratory, read-only DuckDB tools over the embedding cache
(`data/embeddings/*.parquet`, see [embeddings.md](./embeddings.md)). Vectors are
L2-normalized, so **cosine similarity == dot product** and brute force over
~27.5 k books is sub-second — no ANN index needed.

### Load it

```bash
duckdb -init sql/similar.sql          # starts a session with the tools loaded
# or, inside an existing duckdb session:
.read sql/similar.sql
```

This attaches `data/books.duckdb` read-only and defines two views
(`book_meta`, `book_vecs`) and four table macros.

### The views

- `book_vecs` — the working table: one row per embedded book
  (`input_text, work_key, author, title, year, editions, vec`). Deduped to one
  canonical row per embedded string (the most-reprinted edition wins), so it's
  exactly one row per vector (~27.5 k).

### The macros

| macro | what it does |
|-------|--------------|
| `find_book(q)` | resolve a fuzzy title/query to candidate books, most-reprinted first |
| `similar_to(q, n:=10, same_author:=true)` | nearest neighbours to the best match of `q` |
| `similar_between(qa, qb)` | pairwise similarity between two books |
| `recommend([texts], n:=10)` | "because you liked these" — mean cosine to a set |

### Examples

```sql
-- find the canonical row(s) for a title
SELECT * FROM find_book('Hobbit');

-- books like The Hobbit (includes same-author by default)
SELECT * FROM similar_to('The Hobbit', n:=10);

-- theme-first: drop other Tolkien books to see cross-author neighbours
SELECT * FROM similar_to('The Hobbit', n:=10, same_author:=false);

-- how close are two books?
SELECT * FROM similar_between('Brave New World', 'Fahrenheit 451');   -- ~0.83

-- recommend from a liked set (use exact input_text; find them via find_book)
SELECT * FROM recommend(
  ['Book|Aldous Huxley|Brave New World', 'Book|Ray Bradbury|Fahrenheit 451']);
  -- surfaces Nineteen Eighty-Four, The Giver, ...
```

### Gotchas & knobs

- **Author dominance.** The embedded string is `Book|author|title`, so the
  author name is a strong signal — default neighbours skew to the same author.
  Pass `same_author:=false` for a theme-first view.
- **Fuzzy match picks one book.** `similar_to`/`similar_between` resolve each
  query to the single most-reprinted match. Use `find_book(q)` first if you're
  unsure which book you'll get.
- **Near-duplicates.** Editions, omnibuses and adaptations score very high
  (≥ ~0.92, e.g. a graphic-novel *Hobbit*). Treat top hits accordingly.
- **`recommend` needs exact `input_text`** strings (the `Book|author|title`
  form) — look them up with `find_book`.
