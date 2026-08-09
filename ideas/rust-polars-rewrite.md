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
| Go `net/http` ServeMux, `X-User` middleware, JSON handlers | a Rust web server (`tiny_http` sync or `axum` async — see "Web layer") | **clean** — the workload maps directly either way |
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
`ndarray`, `rusqlite`, and whatever web server is chosen (see "Web layer")
will be a few minutes cold and a few seconds incremental — heavier than Go's
sub-second builds but fine for a project this size. Choosing the sync
`tiny_http` profile keeps the web-stack portion of that tree tiny; choosing
`axum` adds the tokio+hyper+tower layer. Enable only the Polars feature flags
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

### The dependency-hell concern, stated honestly

Rust's cargo is *structurally* not npm, and npm-the-company literally chose
Rust for a bottleneck service partly because Cargo's dependency model is
npm-inspired: per-project resolution (no global installs), a single resolver,
and a `Cargo.lock` that pins exact versions + checksums for the whole
transitive tree. You don't get npm-style "my dep wants Lodash@3 and my other
dep wants Lodash@4 so now I have both" duplication silently breaking things —
cargo dedupes where it can and errors (with `cargo tree`) where it can't, and a
checked-in `Cargo.lock` means a binary always builds from the same resolved
tree, even if upstream crates get yanked.

The real "dependency hell" taxes in Rust are different and narrower:

1. **Compile times from deep transitive trees.** tokio + hyper + tower + polars
   is a non-trivial amount of code to compile. It's a one-time cold cost and a
   few-second incremental cost — annoying vs Go's sub-second, not hellish.
2. **The async-runtime split.** `tokio` vs `async-std` are two incompatible
   async runtimes; a crate built for one doesn't compose with the other. This
   is the genuine ecosystem-split trap. The fix is simply: commit to one
   runtime (tokio is the default; axum/hyper are tokio), or **avoid async
   entirely** (the sync option below).
3. **0.x crates with breaking changes.** Avoidable by preferring 1.x crates
   and pinning minor versions in `Cargo.lock`.

None of these are forced by the choice; they're controllable with a curated,
small feature set. The defensive move is to start from a *small* dep tree and
add only when a real need appears.

### The auth question is easy in every option

The current auth model is a **trusted `X-User` header** — read it, 401 if
missing, `EnsureUser` against the store, delegate. That is a header read; it
needs **no library** in any framework. JSON bodies and responses need
`serde`+`serde_json` (2 stable crates, the universal foundation, present in
essentially every Rust binary). So "auth and JSON" do not actually force a
framework choice here. If real auth arrives later (sessions, OAuth, JWT),
that's a separate decision — and there are crate options (`tower-http`'s
`auth`/`ValidateRequest`, `jsonwebtoken`, `oauth2`) for all of them.

### Three concrete profiles

The workload shapes the trade-off: low-volume local service, requests are
mostly blocking `rusqlite` calls + one CPU-bound matmul per recommend request,
with exactly one I/O concurrency point (the on-demand embed HTTP call to
llama-server). That is a *sync-friendly* workload.

**Option 1 — `tiny_http` (sync, minimal deps).**
`tiny-http` is a low-level, fully synchronous HTTP server. A 2024 release
reduced its baseline transitive tree from **18 crates to 5** (the `httpdate`
swap). No async runtime. No tower. No tokio. You match on `request.method()` +
`request.url()`, read the `X-User` header directly, decode the body with
`serde_json::from_reader`, and return a `Response`. JSON + auth cost you
nothing beyond `serde`/`serde_json`. Thread-per-request is built in (its own
`threadpool`).

  - Web-side deps: ~7 (`tiny_http`, `serde`, `serde_derive`, `serde_json`,
    plus a tiny HTTP client for the embed call — `ureq`, sync, no async).
  - **No async anything** → the runtime-split dimension of "hell" simply does
    not exist. `rusqlite` (sync) composes trivially; the matmul blocks a
    thread briefly; the thread pool handles concurrent requests.
  - Cost: routing is hand-rolled (`match` on method + path prefix); no
    extractors/middleware out of the box (you write 5-line helpers). For 6
    endpoints this is fine.

**Option 2 — `hyper` direct (async, no framework) — researched, not being seriously considered.** Skip axum/tower, keep hyper+tokio+`hyper-util` (server). You implement a `Service` and match on `Request` method+path yourself. ~15-25 transitive crates. Still async (tokio), so `rusqlite` calls need `spawn_blocking` and the embed call needs `reqwest` (async). It's a middle ground that takes on the async tax *without* the sqlx type-safety payoff — so it's dominated by one of the other two options for this workload, and has been dropped from contention.

**Option 3 — `axum` (async, the coherent stack).** Pulls tokio + hyper +
tower + tower-http + axum-core, all maintained by the **tokio-rs / tower-rs**
orgs and versioned coherently — one company's stack, not random packages.
Default features give you routing, `Json`/`Query`/`Path` extractors, the
tokio runtime. ~50+ transitive crates but coherent. `tower-http` gives
optional middleware (timeouts, tracing, compression, `auth` helpers) if you
want them later — none are forced.

  - The `{*work_key}` catch-all gotcha maps directly: axum's wildcard
    `/{*key}` strips the leading slash *exactly like Go's* `{work_key...}` —
    "for `/foo/{*rest}` and path `/foo/bar/baz`, `rest` is `bar/baz`." So the
    existing `workKey := "/" + path_value` trick ports verbatim.
  - `X-User` middleware is a 10-line `tower` layer or axum `from_fn`.
  - Cost: the async tax (`async fn`, `Send` bounds on shared
    `Arc<DataFrame>` state, `spawn_blocking` for `rusqlite`), and the compile
    time / tree size of the tokio+hyper+tower layer.

**Explicitly not recommended here.** `actix-web` (its own actor framework,
another runtime on top of tokio, biggest dep tree, historically more API
churn); `rocket` (macro-heavy batteries-included framework, nightly-ish); and
**anything `async-std`-based** (e.g. Tide) — picking async-std over tokio is
literally where the async-runtime-split hell comes from. Stay tokio, or stay
sync.

### Sync vs async for *this* workload — the two stacks we're seriously considering

These are **the two stacks we're seriously considering**: Option 1 (sync) and
Option 3 (async). The honest call on the workload: it's low-volume, the DB is
sync (`rusqlite`), the heavy path is a CPU matmul, and the only async I/O that
*might* pay off is the on-demand embed call — and even that is one short HTTP
POST per request at worst. **A sync `tiny_http` service is genuinely
sufficient** and is the depend-lightest, no-async-runtime, no-tower choice.
It removes the entire async dimension of the dependency-hell concern up
front. The preference-clustering extension (k-means over a few dozen vectors)
is pure CPU blocking work — fits sync perfectly; in axum it'd want
`spawn_blocking`.

`axum` earns its larger tree in two cases: if you'll value the `tower-http`
middleware ecosystem later (timeouts, tracing, compression, auth helpers), or
if you specifically want **compile-time-checked SQL via `sqlx`** — the one DB
feature that's async-only (see Migrations & type-safety below) and so tips you
onto the async stack. Otherwise it's more machinery than this workload
strictly requires. Its dep tree is coherent (tokio-rs, one org), not JS-style
random.

### Migrations & type-safety — orthogonal to the framework choice

A key point worth pulling out: **none of the web frameworks help (or hurt) the
database story.** axum, tiny_http, hyper care about HTTP; they never see your
SQLite handle. Migrations and type-safe queries are the job of the *SQL layer*,
a separate crate you pick regardless of which web framework fronts it. So the
DB-tooling decision can be made independently — and it's a notably smaller
decision than the framework one.

**Migrations.** The existing repo runs idempotent `CREATE TABLE IF NOT EXISTS`
SQL in `internal/db/migrations.sql` on every boot (see `internal/db/db.go`).
Two lightweight Rust replacements fit that same "schema lives in the repo"
ethos:

- **`rusqlite_migration`** — the lightweight, sync, rusqlite-native option.
  Tracks state in SQLite's built-in `user_version` integer (no extra table),
  no DSL, just SQL strings in a `const MIGRATIONS: &[M] = &[M::up("..."),
  ...]` slice, applied with `MIGRATIONS.to_latest(&mut conn)` at startup.
  Tiny crate, no macros, no CLI. The closest thing to a drop-in for the
  current “run `migrations.sql` on boot” pattern. Supports a `from-directory`
  feature if you want `V1__init.sql` files instead of inline strings.
- **`refinery`** — slightly heavier; supports `.sql` files or Rust modules,
  named `V{n}__{name}.sql`, embedded via `embed_migrations!("./migrations")`.
  Works with rusqlite and Postgres/MySQL, but you only need SQLite here. A
  reasonable alternative if you want file-per-migration on disk.
- **`sqlx`'s `migrate!`/`Migrator`** — described below with sqlx; tied to the
  async sqlx driver.

**Type-safe queries.** Three tiers of strength vs. machinery:

- **Plain `rusqlite` + serde.** Write SQL strings, bind with `params![...]`,
  `query_row(...)` / `prepare(...).query_map(...)`, extract columns into
  structs manually. Zero additional deps (rusqlite already required), zero
  compile-time checking. Pairs with `serde_rusqlite` (a tiny helper) or
  `rusqlite::types::FromSql`/`ToSql` derives if you want `vec -> Vec<f32>`
  and similar to deserialize cleanly. This is the lowest-dependency answer and
  matches the spirit of "type safety would be nice but isn't essential" — you
  get it for the row→struct mapping via a derive if you want, but you keep
  writing plain SQL.
- **`sqlx` with the `query!`/`query_as!` macros** — the headline Rust feature:
  **compile-time checked SQL**. The macro connects to a dev database (or reads
  an offline `.sqlx/` cache you checked into git, produced by
  `cargo sqlx prepare`) and validates column names, parameter count and types
  against your real schema at build time — a type error if your SQL drifts
  from the schema. `query_as!(MyStruct, "SELECT ...")` maps rows to your
  struct by column name. This is the strongest type safety in the Rust
  ecosystem and it's genuinely nice.
  - **The catch: sqlx is async-only.** Every call is `.await`-able, it pools
    connections under tokio, and it pulls the tokio+sqlx runtime. That
    *couples two decisions that were otherwise independent* — choosing sqlx
    for type-safety forces the **async** web profile (axum), ruling out the
    sync `tiny_http` option. It also re-introduces the
    `libsqlite3-sys` version-hazard (sqlx and rusqlite both link
    libsqlite3-sys; sqlx's docs explicitly warn to pin both versions together
    to avoid `cargo update` breakage).
  - So sqlx is the right pick **if** you've already decided on the async stack
    for other reasons (axum + tokio). If you went sync `tiny_http`, sqlx
    doesn't fit the sync model and you'd use plain `rusqlite` instead.
- **Full ORMs (SeaORM, Diesel)** — overkill here. Diesel is sync, mature, and
  provides a DSL + strong compile-time checking, but it's the heaviest DB
  dependency you could add and its query DSL fights ad-hoc SQL. SeaORM is
  async-on-sqlx. Neither earns its complexity for a ~3-table app-state schema.

**Net for the small schema at hand** (three tables: `users`, `ratings`,
`ondemand_vecs`, ~4 queries each): `rusqlite` + `rusqlite_migration` is the
low-friction pairing. It keeps the sync model viable (matches `tiny_http`),
bundles a real migration runner that replaces the boot-time idempotent-DDL
hack, and gets you row→struct type-safety via `serde_rusqlite` or hand-written
conversions. Compile-time SQL checking via sqlx is appealing but it *couples*
to the async-framework decision — so it's really an axum-stack decision, not a
DB decision.

### What actually moves with the framework choice

| Concern | Picked by | Notes |
|---|---|---|
| DB schema migrations | `rusqlite_migration` or `refinery` (sync); `sqlx::migrate!` (async) | orthogonal to web framework |
| Compile-time SQL type-checking | `rusqlite` (none) or `sqlx` macros | sqlx forces the async stack |
| Row → struct | `serde_rusqlite`, `#[derive(FromSql)]`, or sqlx `query_as!` | sqlx gives the nicest version |
| Connection handling | rusqlite: single conn under a `Mutex`, like today; sqlx: `SqlitePool` | current code already single-conn |
| The web framework | **Sync: `tiny_http`** or **Async: `axum`** | hyper-direct dropped; ruled out below |

### Performance under bursts (the interesting question)

The instinct is "async handles bursts better" — and for *I/O-bound* workloads
that's true. This app is not that, and the actual bottleneck map changes the
answer:

1. **The recommend endpoint is CPU-bound, not I/O-bound.** The work is a
   matmul of the ~28k×4096 corpus against a ~4096-dim centroid (and
   eventually a k means step over a few dozen vectors). That's synchronous CPU
   work — async/`.await` accelerates nothing; in axum you'd `spawn_blocking`
   it onto the blocking thread-pool anyway, which is just *threads doing the
   work* — exactly what tiny_http does natively. Either stack tops out at
   ~Ncpu concurrent recommend requests; extra queued requests wait for a
   core. The HTTP framework choice has essentially **zero bearing** on the
   heavy endpoint's burst ceiling: both are CPU-saturated.

2. **The SQLite writes are single-writer in both stacks — and async SQLite
   can be *worse*.** SQLite is single-writer in any journal mode; WAL only
   lifts the reader/writer exclusion so readers don't block the (one) writer.
   A widely-cited 2026 benchmark (`emschwartz.me`) measured sqlx with a 50-
   connection pool writing concurrently at **2,586 rows/sec, p99 = 182s** —
   worse than catastrophic — because 50 connections all fought for the writer's
   EXCLUSIVE lock. The same workload with a **single writer connection**
   (writes queued at the app level) ran at **60,061 rows/sec, p99 = 82ms** —
   ~20× faster. The SQLite docs themselves recommend the single-writer+
   many-reader topology; the async-execution-patterns writeup makes the same
   prescription (one writer, `BEGIN IMMEDIATE`, `busy_timeout`, manual
   checkpointing). **rusqlite's single-`Mutex` pattern *is* that single-writer
   topology, by construction** — which is exactly the pattern the current Go
   code already uses (`SetMaxOpenConns(1)` + `sync.Mutex`). So on a write
   burst (many `PUT /api/preferences` at once) the **sync stack gives you the
   recommended SQLite topology for free**, while the async sqlx stack requires
   you to deliberately constrain the pool (or wrap writes in a `Mutex` /
   `spawn_blocking` channel) to avoid the contention trap.

3. **For the light endpoints (`/health`, `/api/books` search) and connection
   floods, axum/tokio is genuinely more robust.** tokio multiplexes thousands
   of concurrent connections over a small worker pool (≈Ncpu threads, bounded
   task queue, backpressure via async readiness). tiny_http is **thread-per-
   request with an unbounded thread pool** — idle threads die after 5s, but
   on a connection burst it spawns a thread per connection with no ceiling
   (a known frailty: see tiny-http issue #221, "Use bounded thread pool",
   which discusses DoS / FD exhaustion / fall-over-under-load). A burst of, say,
   10k concurrent keep-alive connections on axum is a non-event; on tiny_http
   it's 10k OS threads (~GBs of stack) and you'd want to raise `ulimit -n` and
   tune, or put a reverse proxy in front. For low personal volume this is
   academic, but if a CDN / browser fan-out realistically arrives, it's the
   one place the async stack pulls materially ahead.

4. **What does the front-end actually do?** The React frontend issues one
   `/recommendations` call per page load plus a handful of `/api/books`
   searches and `/api/preferences` updates. It is a single user's browser,
   not a fleet of clients. The realistic "burst" is 1–3 concurrent requests —
   well within either stack's trivial envelope. The hypothetical-burst
   scenario above is a "what if it goes viral / gets deployed behind a load
   balancer someday" question, not the actual workload.

**Net.** For the heavy CPU endpoint and for write bursts, the two stacks are
roughly equal (and the sync stack is arguably *better* on SQLite writes,
because it gives the recommended topology by default). The one place async
wins clearly is a flood of concurrent connections to light endpoints —
thread-per-connection grows memory-hungry and has a known exhaustion
weakness; tokio handles it gracefully. That's a real consideration for a
public-facing service and a non-event for a personal one — it's the deciding
factor only if you anticipate the service going multi-user someday.

### Compile times vs Go

The Go `cmd/serve` builds in well under a second. A Rust binary pulling
`polars` + `ndarray` + `rusqlite` + (web stack) will be a few minutes cold
and a few seconds incremental, depending on feature flags. This is the main
"Go is better for web services" tax, already accepted. Keeping the web stack
small (Option 1) trims both cold build time and `Cargo.lock` size noticeably
versus Option 3.

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
  serve/                    # HTTP service (tiny_http or axum): handlers, init
  data/                     # Polars corpora loaded at boot (catalog, corpus),
                            #   centroid + ranking (ndarray), search
  store/                    # the mutable-store trait + impls
  embed/                    # llama-server HTTP client + normalize + sha256
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

1. **App-state store**: SQLite (pragmatic, bundled C lib but small), Delta
   (Parquet-native ACID + upsert, but pulls DataFusion and is async-heavy for a
   tiny write volume), or a pure-Rust KV (`redb`/`fjall`, no C, but
   non-tabular)? **Settled since this conversation: SQLite**, keeping
   Parquet folders for the corpus/embeddings.
2. **Web stack — two serious contenders, drop hyper-direct.** The choice
   forks cleanly because sqlx (compile-time SQL type-safety) is async-only, so
   picking it for its type-safety *forces* the async side. Two stacks to pick
   between:
   - **Sync stack** = `tiny_http` + `rusqlite` + `rusqlite_migration` (+
     `serde_rusqlite`/derives for row→struct). ~7 web deps, no async runtime,
     no tower. Row→struct safety yes; **compile-time SQL checking no**. Genuinely
     sufficient for "JSON in, JSON out, one header auth, one CPU matmul per
     request" at low volume; removes the async-runtime dimension of
     dependency-hell up front.
   - **Async stack** = `axum` + tokio + `sqlx` (sqlite) + `sqlx::migrate!`.
     Larger coherent dep tree (tokio-rs org, one stack not random), gives
     **compile-time-checked SQL** via the `query!`/`query_as!` macros (the
     strongest type-safety in the Rust ecosystem) plus the `tower-http`
     middleware ecosystem for later.
   `hyper`-direct is out — it took on the async tax without the sqlx
   type-safety payoff, so it's dominated by one of the two above for this
   workload. `actix-web`, `rocket`, and anything `async-std`-based remain
   explicitly ruled out. **On bursts** (see "Performance under bursts"):
   neither stack wins on the heavy CPU matmul or on SQLite writes (sync stack
   is arguably *better* on SQLite writes since its single-`Mutex` pattern *is*
   the recommended single-writer topology); the async stack's one clear edge is
   robustness under a flood of concurrent lightweight connections — relevant
   only if the service ever goes multi-user / public-facing.
3. **Extraction pipeline**: hand-roll a Rust extractor (`serde_json` + `rayon`)
   for a fully DuckDB-free repo, or keep `bin/extract.sh`/`bin/clean.sh` on the
   DuckDB CLI as an infrequent batch tool that *outputs Parquet*? The latter
   removes DuckDB from the runtime and the file format without a risky JSON
   port. Settled-ish: it's a batch job, can stay on whatever — decision
   deferred.
4. **Embedding driver**: keep `bin/embed.py` (lowest risk) or rewrite in Rust
   for a one-language repo?
5. **Polars feature flags**: pin the minimal set (`dtype-array`, `lazy`,
   `parquet`, `csv`, `strings`, `is_in`, `round_series` if needed) to keep
   compile time and binary size sane.
6. **Does this unblock preference clustering?** Yes — k-means over liked
   vectors becomes ~20 lines of `ndarray` server-side, removing the "no native
   k-means in the DB engine" friction noted in `ideas/preference-clusters.md`.
   Worth calling out as a side-benefit of the rewrite.