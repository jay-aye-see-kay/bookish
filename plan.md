# Plan: generate & cache book embeddings

Goal: embed ~27.6k popular books with Qwen3-Embedding-8B and store the vectors
in a durable, standalone, pass-through cache that survives any future
restructuring of `books.duckdb`.

The embedding input is **`"Book|${author}|${title}"`** (e.g.
`Book|Isaac Asimov|I, Robot`) — the `Book|` scaffold disambiguates type so the
decoder reaches for its *book* knowledge, not a same-named film/song; title is
last so the most-identifying token sits nearest the `last`-pooled position. We
want the model's *knowledge* of the book, not its blurb.

---

## Established facts (from prior investigation — don't re-benchmark)

- **Model**: `Qwen3-Embedding-8B-Q8_0.gguf` (7.5 GB), downloaded at
  `~/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-8B-GGUF/snapshots/*/Qwen3-Embedding-8B-Q8_0.gguf`.
- **Output**: 4096-dim float32 vectors. Correct pooling = **`last`** (Qwen3 is a
  causal decoder; `cls` gives wrong vectors). Normalize L2 so cosine = dot.
- **Throughput ceiling ≈ 28 emb/s** on this M1 Max. It is **memory-bandwidth
  bound** on the 8 GB weight read per forward pass. Verified dead ends (do NOT
  retry): `-np`/packing makes it *slower* (12/s), server continuous batching &
  HTTP concurrency give no gain, `-ub` size is irrelevant. Single-stream is best.
- **Run time**: 27.6k books ≈ **~16 min**.
- **Sandbox note**: Metal/GPU is blocked inside the pi sandbox, so the
  **llama-server runs OUTSIDE the sandbox** (user starts it). The driver reaches
  it over `http://localhost:8080` (allowed; `no_proxy` covers localhost). The
  driver itself can run inside or outside the sandbox.
- **DuckDB verified**: writes/reads `FLOAT[4096]` fixed arrays to Parquet
  losslessly; `list_cosine_similarity()` works natively; ~14.6 KB/vec →
  **27.6k ≈ ~395 MB** float32. No ANN index needed at this scale (brute-force
  cosine is sub-second).

---

## Step 0 — flake.nix: add Python with packages

The dev shell currently has only Go + duckdb. Add a Python with the driver's
deps. Edit `flake.nix`, in the `packages = with pkgs; [ ... ]` list, add:

```nix
              duckdb
              (python3.withPackages (ps: with ps; [
                duckdb    # read books.duckdb, hash, parquet
                pyarrow   # write parquet parts (fixed_size_list<float32,4096>)
                numpy     # L2-normalize vectors
                requests  # talk to llama-server /v1/embeddings
              ]))
```

Then `direnv reload` (or `nix develop`) picks it up. Verify:
`python3 -c "import duckdb, pyarrow, numpy, requests; print('ok')"`.

---

## Step 1 — the embedding server (user runs OUTSIDE sandbox)

`bin/serve-embed.sh` must start the server with **pooling last**. Ensure it is:

```bash
llama-server -m "$MODEL" --embedding --pooling last -ub 8192 \
  --host 127.0.0.1 --port 8080
```

(Client L2-normalizes anyway, so `--embd-normalize` on the server is not
relied upon.) Wait for `server is listening`. Sanity:
`curl -s localhost:8080/v1/embeddings -H 'Content-Type: application/json' \
 -d '{"input":"Book|J.R.R. Tolkien|The Hobbit","model":"q"}' | ...` → 4096 dims.

---

## Step 2 — the input set (27.6k books)

Exact query (verified count = **27,652**, all pre-2025, all have an author name):

```sql
SELECT b.work_key, 'Book|' || a.name || '|' || b.title AS input_text
FROM books b
JOIN authors a ON a.author_key = b.primary_author_key
WHERE b.description IS NOT NULL
  AND b.first_publish_year IS NOT NULL
  AND b.edition_count >= 10
  AND b.first_publish_year < 2025;
```

Notes/decisions:
- Uses `primary_author_key` (one row per book). Subtitle is intentionally
  **excluded** — just `Book|author|title`.
- `books.duckdb` is often **locked** by another duckdb process. Open it
  **read-only** (`duckdb.connect(path, read_only=True)` /
  `duckdb -readonly`) to avoid lock conflicts.

---

## Step 3 — the cache (durable, pass-through, decoupled from books.duckdb)

**Format**: a directory of Parquet part files: `data/embeddings/part-*.parquet`
(DuckDB globs `data/embeddings/*.parquet` transparently). Never deleted;
append-only. `data/embeddings/` is git-ignored (large; lives under `data/`).

**Key on the INPUT, not `work_key`** so restructuring books.duckdb never
invalidates it, and identical `title - author` strings dedupe.

Schema per row (pyarrow):

| column         | type                          | notes |
|----------------|-------------------------------|-------|
| `input_text`   | `string`                      | exact string embedded |
| `input_sha256` | `string` (hex)                | **cache key** = `hashlib.sha256(input_text.encode()).hexdigest()` |
| `model_id`     | `string`                      | `"Qwen3-Embedding-8B-Q8_0/last/norm2/v2-book-pipe"` — invalidates on config/format change |
| `vec`          | `fixed_size_list<float32,4096>` | L2-normalized |
| `created_at`   | `timestamp[us]`               | |

Cache identity = `(input_sha256, model_id)`. `work_key` is deliberately NOT
stored — it's joined back at query time from `books.duckdb`.

DuckDB `sha256(text)` matches Python `hashlib.sha256(utf8).hexdigest()`
(both hex over UTF-8 bytes) — verify once so SQL-side joins line up.

---

## Step 4 — the driver: `bin/embed.py`

Idempotent, resumable, pass-through. Pseudocode:

```python
CACHE_DIR = "data/embeddings"
MODEL_ID  = "Qwen3-Embedding-8B-Q8_0/last/norm2/v2-book-pipe"
URL       = "http://localhost:8080/v1/embeddings"

# 1. inputs from books.duckdb (read-only)  -> [(work_key, input_text)]
#    dedupe by input_text; compute input_sha256 per unique string.

# 2. load already-cached keys for this MODEL_ID:
#    duckdb: SELECT input_sha256 FROM 'data/embeddings/*.parquet'
#            WHERE model_id = ?    (handle "no files yet")
#    misses = unique inputs whose sha not in cached set.

# 3. embed misses via server, batch=32, sequential (concurrency gives no gain):
#    POST {"input": [batch of texts], "model": "q"} -> data[i].embedding
#    L2-normalize each vec (numpy). Order is preserved.
#    Flush a new part file every ~1000 vectors (pyarrow.parquet.write_table
#    with fixed_size_list<float32,4096>) so an interrupted run resumes cheaply.
#    Print progress + running emb/s (~28/s expected, ~16 min total).

# 4. done. Re-running with nothing new is a no-op (all cached).
```

Key details:
- **Batch/concurrency**: `batch=32, concurrency=1`. Proven optimal; do not add
  concurrency (no gain, adds complexity).
- **Resumability**: because misses are recomputed from the cache each run, a
  crash just means rerun; already-written parts are skipped.
- **Normalization**: L2-normalize client-side (`v /= np.linalg.norm(v)`), so we
  don't depend on the server flag and cosine == dot product downstream.
- **Server-down handling**: fail fast with a clear message if
  `GET /health` isn't `ok`.

---

## Step 5 — verify

```sql
-- count & dim
SELECT count(*), any_value(array_length(vec)), any_value(model_id)
FROM 'data/embeddings/*.parquet';

-- join back to books and eyeball a nearest-neighbour sanity check
WITH e AS (SELECT * FROM 'data/embeddings/*.parquet'),
     q AS (SELECT vec FROM e WHERE input_text = 'Book|J.R.R. Tolkien|The Hobbit')
SELECT e.input_text, round(list_cosine_similarity(e.vec, q.vec),3) sim
FROM e, q ORDER BY sim DESC LIMIT 10;
```

Expect ~27.6k rows (minus any exact `title-author` dupes), dim 4096, and the
Hobbit's neighbours to be plausibly related titles.

---

## Step 6 — docs

Per `AGENTS.md` ("keep docs up to date"):
- Update `docs/dev/extracting-data.md` (or a new `docs/dev/embeddings.md`) with:
  the cache location/schema, the ~28 emb/s ceiling, the serve + embed commands,
  the `Book|author|title` input format rationale (type disambiguation for the
  decoder's knowledge), and the `>=10` filter rationale (+2025 cutoff = model
  training cutoff).
- Mention `bin/serve-embed.sh` (pooling last) and `bin/embed.py` in the layout.
- Add `data/embeddings/` to `.gitignore` if not already covered by `data/`.

---

## Open decisions (defaults chosen; change if desired)

- **float32** (~395 MB). Could switch to float16 (~200 MB) — negligible
  retrieval impact — if size matters. Default: float32.
- **Filter tier fixed at `>=10` (27.6k)** per instruction. The same driver
  trivially scales to `>=5` (75k, ~45 min) later — the cache dedupes/reuses.
- Existing bench scripts (`bin/bench-embed.sh`, `/tmp/q.py`,
  `/tmp/bench-server.py`) can be deleted or kept; they're only for benchmarking.
```
