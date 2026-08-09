# Rewriting the backend in Rust + Polars

## Goal

Replace the Go + embedded-DuckDB web backend with a Rust service that uses
[Polars](https://docs.pola.rs/) (the Rust-native DataFrame library) for all the
bulk data work, and a Parquet-based storage layer (plain Parquet, or something
built on Parquet such as Delta Lake) instead of DuckDB files. The motivation:
Rust is a liked language, Polars is mature and has first-class Rust support
(its native implementation *is* Rust; the Python API wraps it), and dropping
DuckDB removes the CGO dependency, the single-writer lock, and the DuckDB file
format from the stack. Go is acknowledged as the better *web-service* language,
but Rust is otherwise preferred and perfectly serviceable here.

This document is an investigation, not a decision. It maps every piece of the
current system to its Rust+Polars equivalent, flags where the fit is clean and
where it hurts, and surfaces the storage-format choice that has to be made.

## The current system, at a glance

What exists today (see `docs/dev/web-backend.md`, `extracting-data.md`,
`embeddings.md`):

1. **Extraction pipeline** (`bin/extract.sh`, `bin/clean.sh`) — DuckDB SQL that
   reads the gzipped Open Library "all types" TSV dump (~120 GB uncompressed)
   directly, does heavy ad-hoc JSON-path extraction
   (`json_extract_string`, `json_extract`, `json_type`, casts to
   `VARCHAR[]`), and builds `books.duckdb` (~759k works, ~338k authors,
   ~890k book_authors). Run only when re-ingesting a new dump.

2. **Embedding driver** (`bin/embed.py`, Python) — reads `books.duckdb`
   read-only, builds `Book|author|title` strings, POSTs them to a local
   llama-server, L2-normalizes, and writes a pass-through Parquet vector cache
   (`data/embeddings/part-*.parquet`, `fixed_size_list<float32,4096>`).

3. **Web backend** (`cmd/serve` in Go + `internal/{db,api,embed}`) — one
   embedded-DuckDB connection, two attached DBs (`books.duckdb` read-only,
   `app.duckdb` writable), materializes a `corpus` (~28k vectors, ~450 MB RAM)
   and a `catalog` (~759k searchable rows) at boot, then serves a small JSON
   API: `/api/health`, `/api/books` (ILIKE search), `/api/preferences` (CRUD),
   `/api/recommendations` (signed-weighted centroid ranked by cosine over the
   corpus). Out-of-corpus likes are embedded on demand and cached in
   `app.ondemand_vecs`.

Three distinct DuckDB roles: (a) batch ETL tool, (b) read-only catalog/corpus
store, (c) writable OLTP-ish app state. A port has to address all three.

## Mapping each piece to Rust + Polars

| Current (Go + DuckDB) | Rust + Polars equivalent | Fit |
|---|---|---|
| `read_parquet('data/embeddings/*.parquet')` glob + `list_cosine_similarity` | Polars `scan_parquet` (lazy, globs natively) over the same Parquet dir; vectors as an `Array<Float32, 4096>` column (Arrow `FixedSizeList`, behind the `dtype-array` feature flag) | **clean** — same Parquet files work unchanged; Polars has the fixed-size-array dtype that matches DuckDB's `fixed_size_list` |
| `corpus` materialized table (vec + metadata join) | `LazyFrame` joining parquet cache → catalog on `input_text`, cached in an `Arc<DataFrame>` at boot | **clean** — hash join over ~28k × ~759k is trivial; hold the result in memory |
| `catalog` (~759k) + `ILIKE` search | Polars `str.contains` (substring) over lowercased columns, `.sort_by(editions).head(n)`; precompute `author_lc`/`title_lc` exactly as today | **clean** — full scan per query, same as DuckDB; sub-100ms at 759k |
| Signed-weighted centroid (SQL `unnest`/`GROUP BY idx`/`sum`) | Pull liked vectors into an `ndarray::Array2<f32>` (Polars `to_ndarray`), `weighted = &vecs * &weights`, `centroid = weighted.sum_axis(Axis(0))` | **cleaner than SQL** — the unnest/group/sum acrobatics become one matrix expression |
| `list_cosine_similarity` ranking + `ORDER BY ... LIMIT n` | `scores = corpus_mat.dot(&centroid)` (vectors are L2-normalized → dot == cosine), `argsort` / `top_k` | **cleaner than SQL** — one matmul over ~28k × 4096, faster to write *and* to run than list_cosine_similarity |
| `all_vecs = corpus UNION ALL ondemand_vecs` view | Concatenate the on-demand vectors (from whatever mutable store holds them) into the liked-vectors matrix at query time | **clean** — it's just an array concat before the centroid calc |
| `app.duckdb` writable (users, ratings, ondemand_vecs) | **This is the real decision — see "Mutable app state" below** | **the crux** |
| Go `net/http` ServeMux, `X-User` middleware, JSON handlers | `axum` (tokio-rs), `tower` middleware, `serde`/`serde_json` extractors | **clean** — see "Web layer" below |
| `internal/embed` llama-server HTTP client | `reqwest` (blocking or async) + the same sha256 + L2-normalize | **clean** — port is mechanical |
| `bin/embed.py` driver (Python) | Either keep as Python, or rewrite in Rust over the new catalog store | **optional** — see "Embedding driver" below |
| `bin/extract.sh` / `bin/clean.sh` (DuckDB SQL, heavy JSON extraction) | **The hardest port — see "The extraction pipeline" below** | **the friction** |

## Where Rust + Polars is genuinely *better*

- **Vector math leaves SQL.** The centroid and ranking are matrix operations.
  Today they're encoded as DuckDB `unnest ... WITH ORDINALITY ... GROUP BY idx`
  + `list_cosine_similarity` — clever but awkward. In `ndarray` they're
  `centroid = (vecs * weights).sum_axis(Axis(0))` and
  `scores = corpus_mat.dot(&centroid)`. One BLAS-ish matmul over ~28k × 4096 is
  fast and obvious.
- **The preference-clustering friction goes away.** `ideas/preference-clusters.md`
  notes the current blocker: "no native k-means in the database engine." In
  Rust, k-means over a few dozen 4096-dim vectors is ~20 lines of `ndarray`
  (assignment = `argmax` of `vecs.dot(centroids)`, update = group-mean +
  renormalise). Spherical k-means falls out for free because vectors are
  L2-normalized. The clustering step that had to be punted to external Python
  becomes natural server-side code.
- **No CGO, no single-writer lock.** The go-duckdb driver requires CGO and a
  prebuilt libduckdb; `app.duckdb` is single-writer so the Go code serializes
  all writes behind a mutex and a running server holds the file lock. A Parquet
  + separate mutable-store design removes both.
- **Polars is pure Rust** (built on the `arrow-rs` Rust Arrow implementation; no
  C/C++ dependency). One static binary, no `clang`/libduckdb in the nix shell.
- **The Rust Polars API is the native one.** Unlike many "Rust bindings for X
  ecosystems," Polars *is* a Rust library and the Python API wraps it — so the
  Rust API is first-class, not a lagging second citizen.

## Where it hurts

### The extraction pipeline (the real friction)

`bin/extract.sh` and `bin/clean.sh` lean hard on DuckDB's JSON family:

```sql
json_extract_string(column4, 'name')
json_extract_string(json, '$.authors[0].author.key')
json_type(json_extract(json,'description')) = 'OBJECT'   -- {type,value} vs plain string
TRY_CAST(json_extract(json,'subjects') AS VARCHAR[])      -- array extraction
regexp_extract(json_extract_string(json,'publish_date'), '\d{4}')
QUALIFY row_number() OVER (PARTITION BY ... ORDER BY edition_count DESC) = 1
```

These run over ~112M rows (15M authors + 41M works + 56M editions) of raw JSON.
On the Polars side:

- **Polars can read the gzipped TSV streaming.** `scan_csv` with the streaming
  engine does streaming gzip decompression (merged ~early 2026, PR #25842);
  it's tested at 100MB-per-row line lengths, so the 40 MB `max_line_size` that
  DuckDB needed is a non-issue. Throughput is in the right ballpark (minutes,
  not hours).
- **But Polars JSON extraction is materially weaker than DuckDB's.** Polars has
  `str.json_decode(dtype)` (requires a schema up front) and
  `str.json_path_match("$.path")`, but it's stricter and less forgiving than
  DuckDB's ad-hoc `json_extract`/`json_type`/`TRY_CAST AS VARCHAR[]` family —
  the mixed-dtype and "object vs plain string" branching that the clean step
  relies on is the kind of thing that produces nulls or errors in Polars and
  "just works" in DuckDB.
- **The realistic Rust path is hand-rolling the extractor** with `serde_json`
  over a `rayon`-parallel scan of the decompressed TSV. That's a real rewrite
  (two SQL scripts → a Rust binary with typed structs for works/editions/authors
  and the fiction/edition-count/dedupe logic). Doable, but it's the biggest
  chunk of new code in the port.
- **Pragmatic alternative:** keep the *extraction* step on the DuckDB CLI as a
  one-time batch tool (it only runs when re-ingesting a new dump), but have it
  **write Parquet** instead of `books.duckdb`. The downstream Rust service then
  only touches Parquet. This doesn't fully eliminate DuckDB from the repo, but
  it removes it from the *runtime* and from the file format — which is what the
  user actually asked for ("move away from DuckDB as a *file format*"). The
  extraction tool being a batch CLI that runs twice a year is a reasonable place
  to leave DuckDB if the Polars JSON story proves painful in practice.

### Mutable app state (the storage decision)

Polars is a DataFrame/analytics library, not an OLTP store. The
`users`/`ratings`/`ondemand_vecs` tables need upserts, point reads, deletes, and
crash-safety — none of which Parquet or Polars do natively. Realistic options:

- **SQLite via `rusqlite` (with the `bundled` feature).** The most direct 1:1
  replacement for `app.duckdb`'s role. `bundled` compiles SQLite 3.51 from
  source into the binary — no system SQLite dep, one static binary, ACID, real
  upserts (`INSERT ... ON CONFLICT`), fast point reads/writes, mature
  (rusqlite 0.39, maintained for a decade). Stores the 4096-f32 on-demand
  vectors fine as a BLOB or a typed column. Adds a C dep (bundled SQLite), but
  SQLite is far smaller and more standard than libduckdb, and `bundled` means
  zero external clang/libduckdb chore.
- **Delta Lake via the `deltalake` crate (`delta-rs`).** Mature Rust-native
  Delta implementation (crates.io `deltalake` 0.32.x) with *full* merge/upsert
  support: `DeltaOps(table).merge(source, predicate).when_matched_update(...)
  .when_not_matched_insert(...)`. ACID, time travel, schema enforcement, all
  over Parquet files. This is the "Parquet + ACID + upsert" option the user
  gestured at with "something built on Parquet like Data Lake or Delta."
  Downside: the merge API is **async** (tokio) and builds a source `DataFrame`
  via a DataFusion `SessionContext`, so it pulls in DataFusion — a heavier
  dependency than the rest of the app combined. For a single-process local
  service with low write volume (a handful of ratings per user action), Delta's
  machinery is technically overkill, but it gives a clean Parquet-everywhere
  story and is genuinely mature.
- **A pure-Rust KV store (`redb` or `fjall`).** Both are pure-Rust, ACID, and
  actively maintained. `redb` is B-tree based, LMDB-inspired, stable disk
  format; `fjall` 3.0 is an LSM (RocksDB-like), also stable. Neither is
  tabular, so `ratings` would be modelled as KV pairs (key = `(username,
  work_key)`, value = serialized rating) and queries like "list a user's
  ratings" or "all rated input_texts missing a vector" become prefix/range
  scans over a composite key. Workable and zero-C-dep, but loses SQL's
  ergonomics for the join-y reads. `sled` is explicitly ruled out (unmaintained
  since 2022, no disk-format guarantee).
- **Just Parquet + in-memory.** Append ratings to a Parquet log, load into
  memory at startup, snapshot periodically. Simplest possible, but crash-safety
  for a just-written rating requires an `fsync`-and-snapshot discipline that's
  easy to get wrong; not recommended for the user-facing writes.

**Open question for the user:** is the goal "Parquet everywhere, ACID, upsert"
(recommending Delta), or just "kill DuckDB, keep it simple" (recommending
SQLite)? They give very different dependency graphs. Delta keeps the
Parquet-native story but adds DataFusion; SQLite is the pragmatic minimal
choice but re-introduces a bundled C library (a smaller, more standard one).

### Build & compile-time

Polars is a substantial dependency. A `cargo build` for a binary pulling
`polars` (with `dtype-array`, `lazy`, `parquet`, `csv`, `is_in` features),
`axum`/`tokio`, `ndarray`, and either `rusqlite`/`deltalake` will be a few
minutes cold and a few seconds incremental — heavier than Go's sub-second
builds but fine for a project this size. Enable only the Polars feature flags
actually used (`dtype-array` for the fixed-size vector column, `lazy` +
`parquet` + `csv`, `strings` for search) to keep compile time and binary size
down.

### Polars Rust API feature flags

The fixed-size-list support requires the crate feature **`dtype-array`**. The
`arr` namespace (`.arr.sum()`, `.arr.max()`, `.arr.contains()`) operates on
the `Array` dtype; the older `list` namespace covers the variable-length `List`
dtype. The corpus vectors can and should be `Array<Float32, 4096>`. When
reading the existing parquet cache, the column will arrive as `List` by default
and need an explicit cast to `Array` (or be read with a schema override) to get
the fixed-shape perks.

## Web layer (where Go → Rust)

`axum` (tokio-rs) is the closest thing to Go's stdlib `net/http` in spirit:
handlers are plain `async fn`s, middleware is `tower` layers, and request
bodies/query/path/header are "extractors" — `Query<SearchParams>`,
`Path<String>`, `Json<RatingBody>` — that compose cleanly with serde. It's
maintained under the tokio-rs org, mature, and the de-facto choice for this
kind of small JSON API in 2024-2026 Rust.

A couple of specifics relevant to this codebase:

- **The catch-all path gotcha maps directly.** Go does
  `workKey := "/" + r.PathValue("work_key")` because the `{work_key...}`
  wildcard strips the leading slash. axum's wildcard `/{*key}` behaves
  *identically* — "the leading slash is not included, i.e. for
  `/foo/{*rest}` and the path `/foo/bar/baz` the value of `rest` will be
  `bar/baz`." So the exact same `/`-prepend trick carries over. The route
  `PUT /api/preferences/{*work_key}` with `Path(work_key): Path<String>` and
  `format!("/{work_key}")` is a faithful port.
- **Trusted `X-User` header** is a one-line `tower` middleware that extracts the
  header, 401s if missing, and calls `EnsureUser` (now a storage call) before
  delegating.
- **Concurrency model.** The Go code serializes writes behind a `sync.Mutex`
  only because DuckDB is single-writer. That constraint disappears with any of
  the replacement stores (SQLite in WAL mode, redb's MVCC, Delta's optimistic
  commits). The recommend endpoint's "work" is a CPU matmul; in Rust it's an
  `async fn` that does the compute (with `spawn_blocking` if you want the
  threadpool) and returns JSON. There's no goroutine equivalent, but there
  doesn't need to be — tokio's threadpool handles concurrent requests fine, and
  the per-request work is small.

Compile times: the Go `cmd/serve` builds in well under a second. The Rust
equivalent will build in seconds-to-minutes depending on feature flags. This
is the main "Go is better for web services" tax the user already accepts.

## Embedding driver

`bin/embed.py` is a standalone Python script that talks to llama-server. It's
not part of the running service, and it reads a DuckDB file only to get the
input set. Two options:

- **Keep it in Python** but point it at the new catalog (Parquet via DuckDB CLI
  or Polars Python) instead of `books.duckdb`. Lowest churn; the embed cache
  Parquet schema stays byte-identical.
- **Rewrite in Rust** (`bin/embed.rs`) over the new catalog, using `reqwest` to
  POST llama-server and `arrow`/`parquet` to write part files. Tidier (one
  fewer language in the repo) but the least urgent piece — it's a batch script
  that works.

The on-demand embed path inside the web service (`internal/embed` in Go) ports
mechanically to a `reqwest` client; the L2-normalize and sha256-cache-key logic
are a dozen lines.

## Storage-format decision, summarized

| Concern | Plain Parquet | Delta Lake (`deltalake`) | SQLite (`rusqlite` bundled) | `redb`/`fjall` |
|---|---|---|---|---|
| Vector corpus / catalog (read-only, bulk) | ✅ ideal | ✅ works, overkill | ❌ wrong tool | ❌ wrong tool |
| `app.ratings` / `users` (upsert, point read) | ❌ no upsert | ✅ merge/upsert, async + DataFusion | ✅ native upsert | ✅ via KV modelling |
| `ondemand_vecs` (insert-if-absent, 4096 f32) | ⚠️ append-only log only | ✅ merge | ✅ BLOB | ✅ KV value |
| Crash-safe single write | ⚠️ needs custom fsync | ✅ ACID | ✅ ACID | ✅ ACID |
| Pure-Rust / no C dep | ✅ | ✅ | ❌ bundled C (SQLite) | ✅ |
| Adds heavy deps | none | DataFusion (big) | SQLite (small-ish) | none |
| "Parquet-built" story | ✅ | ✅ | ❌ SQLite file | ❌ custom file |

The likely shape: **plain Parquet for the read-only corpus + catalog**, and
**one** of {SQLite, Delta, redb/fjall} for the mutable app state. The choice is
about the app-state store, not the corpus.

## A possible end-state architecture (for discussion)

```
data/
  catalog.parquet            # ~759k works: work_key, author, title, year,
                            #   editions, input_text, author_lc, title_lc
  embeddings/part-*.parquet  # unchanged: the pass-through vector cache
  books_raw/*.parquet        # the ~759k + 338k + 890k tables (extraction output),
                            #   Parquet instead of books.duckdb
  app/                      # the mutable store (SQLite file, Delta table, or
                            #   redb/fjall db): users, ratings, ondemand_vecs

src/                        # Rust workspace
  serve/                    # axum HTTP service: handlers, middleware, init
  data/                     # Polars corpora loaded at boot (catalog, corpus),
                            #   centroid + ranking (ndarray), search
  store/                    # the mutable-store trait + impls
  embed/                    # llama-server reqwest client + normalize + sha256
  extract/                  # (optional) Rust extractor replacing bin/extract.sh
bin/
  serve.rs ...              # or cargo run -p serve
  embed.py                  # (kept) or embed.rs
```

Startup: `scan_parquet` the catalog + embeddings lazily, join into the corpus
`DataFrame`, hold both in `Arc` for the handlers. Per request: search scans the
catalog; recommendations load the user's rated vectors from the mutable store,
concatenate on-demand vectors, build the centroid in `ndarray`, matmul against
the corpus matrix, top-N. No mutex unless the mutable store needs one.

## Open questions / decisions

1. **App-state store**: SQLite (pragmatic, but a bundled C lib), Delta
   (Parquet-native ACID + upsert, but pulls DataFusion and is async-heavy for a
   tiny write volume), or a pure-Rust KV (`redb`/`fjall`, no C, but
   non-tabular)? This is the biggest architectural choice.
2. **Extraction pipeline**: hand-roll a Rust extractor (`serde_json` + `rayon`)
   for a fully DuckDB-free repo, or keep `bin/extract.sh`/`bin/clean.sh` on the
   DuckDB CLI as an infrequent batch tool that *outputs Parquet*? The latter
   removes DuckDB from the runtime and the file format without a risky JSON
   port.
3. **Embedding driver**: keep `bin/embed.py` (lowest risk) or rewrite in Rust
   for a one-language repo?
4. **Async vs blocking**: if Delta is chosen, the store layer is async/tokio,
   so the service is fully async. If SQLite/KV is chosen, store calls can be
   sync (with `spawn_blocking` in handlers). Pick one model and keep it
   consistent.
5. **Polars feature flags**: pin the minimal set (`dtype-array`, `lazy`,
   `parquet`, `csv`, `strings`, `is_in`, `round_series` if needed) to keep
   compile time and binary size sane.
6. **Does this unblock preference clustering?** Yes — k-means over liked
   vectors becomes ~20 lines of `ndarray` server-side, removing the "no native
   k-means in the DB engine" friction noted in `ideas/preference-clusters.md`.
   Worth calling out as a side-benefit of the rewrite.