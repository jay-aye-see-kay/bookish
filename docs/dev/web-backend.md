# Web backend (Go + embedded DuckDB)

A local JSON backend that turns the book corpus + preference vectors into a
recommender web API. Embedded DuckDB (via `github.com/marcboeker/go-duckdb/v2`,
CGO) reads the parquet vector cache and does all vector math natively
(`list_cosine_similarity`). The embedding model (llama-server) is a separate
process, started outside the sandbox (see below).

## Run

```bash
# 1. (only needed for out-of-corpus likes) OUTSIDE the sandbox: start llama-server
bin/serve-embed.sh

# 2. run the backend (materializes corpus/catalog, then serves JSON on :8090)
bin/serve.sh
```

Recommendations work fully **offline** when every liked book is already in the
27.5k-vector corpus; only likes *outside* the corpus need llama-server (to embed
them on demand).

Env overrides: `BOOKISH_ADDR` (`:8090`), `BOOKISH_BOOKS_DB`
(`data/books.duckdb`), `BOOKISH_APP_DB` (`data/app.duckdb`), `BOOKISH_SERVE_SQL`
(`sql/serve.sql`), `BOOKISH_EMBED_URL` (`http://localhost:8080`).

## Architecture

One in-process DuckDB connection (`SetMaxOpenConns(1)`; DuckDB is single-writer,
so writes are also serialized behind a mutex) with two databases attached:

- `books.duckdb` — **READ_ONLY**, the ~757k-work catalog (see
  `docs/dev/extracting-data.md`).
- `app.duckdb` — writable, holds users / ratings / on-demand vectors.

At startup `sql/serve.sql` **materializes** two tables so per-request work stays
cheap (no re-reading parquet, no re-scanning the 757k window):

- `corpus` — the ~27,500 embedded books with their 4096-dim `FLOAT[]` vectors
  (joined from `data/embeddings/*.parquet` on `input_text`). ~450 MB in RAM.
- `catalog` — every work with an author + publish year (~757k), plus lowercased
  columns for `ILIKE` search.

A view `all_vecs = corpus ∪ app.ondemand_vecs` is the vector source for the
preference centroid.

Vectors are L2-normalized, so cosine == dot. The corpus `vec` is a DuckDB list
(`FLOAT[]`), so use `list_cosine_similarity` (not `array_*`) and cast summed
centroids `::FLOAT[]`.

## Data model (`app.duckdb`)

See `internal/db/migrations.sql` (idempotent, run every boot):

- `users(username PK, created_at)` — auto-created on first request.
- `ratings(username, work_key, input_text, rating ∈ {-2,-1,1,2}, updated_at,
  PK(username, work_key))`. `input_text` (`Book|author|title`) is denormalized
  so the vector join needs no catalog lookup. Magnitude = strength, sign =
  like/dislike; removal is a DELETE (no 0 rating).
- `ondemand_vecs(input_sha256, model_id, input_text, vec FLOAT[], created_at,
  PK(input_sha256, model_id))` — pass-through cache of likes outside the corpus.
  Cache key = `sha256(utf8(input_text))` hex, mirroring `bin/embed.py`.

## Recommendation

A **signed-weighted centroid** of the user's liked/disliked vectors, ranked by
cosine over the corpus. Never exposed. Query lives in
`internal/db/recommend.go`; it reproduces the `recommend()` semantics from
`sql/similar.sql` / `docs/dev/comparing-books.md`, generalized to signed weights.

- No rated book has a vector → `400` (no ratings).
- Weighted centroid ≈ 0 (likes/dislikes cancel) → `409` (degenerate).
- `exclude_rated_authors=true` drops books by any author the user has rated
  (discover-new-authors mode). `Book|author|title` makes author a strong signal,
  so without this flag recommendations skew to already-liked authors.

## Ensure-embedding path (`internal/embed`)

For a liked `input_text`: (1) already in `all_vecs`? use it. (2) else POST
llama-server `/v1/embeddings`, **L2-normalize client-side** (the server returns
UN-normalized vectors), and INSERT into `app.ondemand_vecs`. `MODEL_ID` is reused
verbatim from `bin/embed.py` (`internal/db.ModelID`). On `PUT` this is
best-effort (rating saved even if the server is down); on `GET /recommendations`
any rated book still missing a vector is embedded first, and a failure → `502`.

## HTTP API (JSON, `X-User` header)

Auth is a trusted `X-User` header (no passwords); the user is auto-created.

| Method & path | Body/query | Returns |
|---|---|---|
| `GET /api/health` | — | `{status, catalog, corpus, embed_server:"ok"\|"down"}` |
| `GET /api/books?q=&limit=20` | query | `[{work_key,author,title,year,editions,has_embedding}]` — `ILIKE` over `catalog`, `editions DESC` |
| `GET /api/preferences` | `X-User` | `[{work_key,author,title,year,rating}]` |
| `PUT /api/preferences/{work_key…}` | `{rating:-2\|-1\|1\|2}` | upsert (`404` unknown work_key, `400` bad rating) |
| `DELETE /api/preferences/{work_key…}` | `X-User` | `204` |
| `GET /api/recommendations?n=20&exclude_rated_authors=false` | `X-User` | `[{…,score}]`; `400` no ratings, `409` degenerate, `502` embed failed |

`work_key` in the path includes the `/works/…` prefix, e.g.
`PUT /api/preferences/works/OL64365W`.

## Layout

```
cmd/serve/main.go        entrypoint: open+attach duckdb, run serve.sql, serve
internal/db/             connection (single-writer), migrations, queries, recommend
internal/embed/          llama-server client + L2-normalize + sha256 cache key
internal/api/            handlers, X-User middleware, ensure-embedding wiring
sql/serve.sql            book_meta view + materialize corpus/catalog + all_vecs view
bin/serve.sh             run the backend (llama-server started separately)
```

## Notes / gotchas

- CGO is required (`CGO_ENABLED=1`); the system `/usr/bin/clang` is used. nix
  `mkShellNoCC` only means nix doesn't add a compiler — the system one is on
  PATH. The driver bundles a prebuilt libduckdb (nix `duckdb` is CLI-only).
- DuckDB is single-writer: don't open a second writer to `app.duckdb`. A running
  `serve` holds the lock, so stop it before poking `app.duckdb` with the `duckdb`
  CLI.
- Metal/GPU is blocked inside the sandbox, so llama-server runs OUTSIDE it.
```
