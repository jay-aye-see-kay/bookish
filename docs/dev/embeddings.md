## Overview

Embeds popular books with **Qwen3-Embedding-8B** and caches the vectors in a
durable, append-only, pass-through Parquet cache under `data/embeddings/`.

The embedding input is **`"Book|${author}|${title}"`** (e.g.
`Book|Isaac Asimov|I, Robot`). The `Book|` scaffold disambiguates *type* so the
decoder reaches for its *book* knowledge (not a same-named film/song); the title
is last so the most-identifying token sits nearest the `last`-pooled position.
We want the model's *knowledge* of the book, not its blurb.

## Layout

```
bin/
  serve-embed.sh   starts llama-server (pooling=last) OUTSIDE the sandbox
  embed.py         driver: reads books.duckdb, embeds misses, writes cache
data/
  embeddings/      part-*.parquet — the durable vector cache (git-ignored)
```

## Model & throughput (established, don't re-benchmark)

- **Model**: `Qwen3-Embedding-8B-Q8_0.gguf` (7.5 GB), from HuggingFace cache.
- **Output**: 4096-dim float32. Pooling = **`last`** (Qwen3 is a causal decoder;
  `cls` gives wrong vectors). Vectors are **L2-normalized client-side** so
  cosine == dot product downstream.
- **Throughput ≈ 21–28 emb/s** on this M1 Max; **memory-bandwidth bound** on the
  8 GB weight read per forward pass. Single-stream (`batch=32, concurrency=1`) is
  optimal — `-np`/packing, server continuous batching, and HTTP concurrency all
  give **no gain** (or are slower). Don't retry them.
- **Run time**: ~27.5 k books ≈ **~16–22 min**.
- **Metal/GPU is blocked inside the pi sandbox**, so the **server runs OUTSIDE**
  the sandbox (user starts it). The driver reaches it over
  `http://localhost:8080` (`no_proxy` covers localhost) and can run in or out.

## Usage

```bash
# 1. OUTSIDE the sandbox: start the server (wait for "server is listening")
bin/serve-embed.sh

# 2. anywhere: run the driver (idempotent, resumable)
python3 bin/embed.py
```

`embed.py` reads inputs from `books.duckdb` (read-only, to avoid the lock held by
other duckdb processes), dedupes on `input_text`, skips anything already cached
for the current `model_id`, embeds the misses in batches of 32, L2-normalizes,
and flushes a new part file every ~1000 vectors. A crash just means rerun —
already-written parts are skipped. Re-running with nothing new is a no-op.

## The input set

Verified count = **27,652** rows (→ 27,495 unique `Book|author|title` strings):

```sql
SELECT b.work_key, 'Book|' || a.name || '|' || b.title AS input_text
FROM books b
JOIN authors a ON a.author_key = b.primary_author_key
WHERE b.description IS NOT NULL
  AND b.first_publish_year IS NOT NULL
  AND b.edition_count >= 10      -- popularity tier (see extracting-data.md)
  AND b.first_publish_year < 2025;  -- ~model training cutoff
```

- `primary_author_key` gives one row per book. Subtitle is intentionally
  excluded — just `Book|author|title`.
- `edition_count >= 10` is the chosen popularity tier (~28 k). The same driver
  scales to `>= 5` (~75 k) later — the cache dedupes and reuses everything.
- `< 2025` keeps inputs within the model's training horizon.

## The cache

**Format**: a directory of Parquet part files `data/embeddings/part-*.parquet`
(DuckDB globs `data/embeddings/*.parquet` transparently). Append-only, never
deleted, git-ignored (large; ~449 MB float32 for the 28 k tier).

**Keyed on the INPUT string, not `work_key`** — so restructuring `books.duckdb`
never invalidates the cache, and identical `Book|author|title` strings dedupe.

Schema per row:

| column         | type                            | notes |
|----------------|---------------------------------|-------|
| `input_text`   | `string`                        | exact string embedded |
| `input_sha256` | `string` (hex)                  | cache key = `sha256(input_text utf8)` |
| `model_id`     | `string`                        | `Qwen3-Embedding-8B-Q8_0/last/norm2/v2-book-pipe` — invalidates on config/format change |
| `vec`          | `fixed_size_list<float32,4096>` | L2-normalized |
| `created_at`   | `timestamp[us]`                 | |

Cache identity = `(input_sha256, model_id)`. `work_key` is deliberately NOT
stored — it's joined back at query time from `books.duckdb`. DuckDB
`sha256(text)` matches Python `hashlib.sha256(utf8).hexdigest()`, so SQL-side
joins line up.

## Querying

```sql
-- count & dim
SELECT count(*), any_value(array_length(vec)), any_value(model_id)
FROM 'data/embeddings/*.parquet';

-- nearest neighbours (cosine == dot since normalized)
WITH e AS (SELECT * FROM 'data/embeddings/*.parquet'),
     q AS (SELECT vec FROM e WHERE input_text = 'Book|J.R.R. Tolkien|The Hobbit')
SELECT e.input_text, round(list_cosine_similarity(e.vec, q.vec),3) sim
FROM e, q ORDER BY sim DESC LIMIT 10;
```

Brute-force cosine over ~28 k vectors is sub-second — no ANN index needed at
this scale. Join back to `books.duckdb` on `input_text` to recover `work_key`.
