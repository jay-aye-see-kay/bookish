#!/usr/bin/env python3
"""Generate & cache book embeddings (Qwen3-Embedding-8B, pooling=last).

Idempotent, resumable, pass-through cache keyed on the *input string* (not
work_key) so it survives any restructuring of books.duckdb. See plan.md.
"""

import hashlib
import os
import sys
import time
from datetime import datetime, timezone

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import requests

CACHE_DIR = "data/embeddings"
MODEL_ID = "Qwen3-Embedding-8B-Q8_0/last/norm2/v2-book-pipe"
URL = os.environ.get("EMBED_URL", "http://localhost:8080/v1/embeddings")
HEALTH = URL.rsplit("/v1/", 1)[0] + "/health"
BOOKS_DB = "data/books.duckdb"
DIM = 4096
BATCH = 32
FLUSH_EVERY = 1000

INPUT_QUERY = """
SELECT b.work_key, 'Book|' || a.name || '|' || b.title AS input_text
FROM books b
JOIN authors a ON a.author_key = b.primary_author_key
WHERE b.description IS NOT NULL
  AND b.first_publish_year IS NOT NULL
  AND b.edition_count >= 10
  AND b.first_publish_year < 2025;
"""

SCHEMA = pa.schema([
    ("input_text", pa.string()),
    ("input_sha256", pa.string()),
    ("model_id", pa.string()),
    ("vec", pa.list_(pa.float32(), DIM)),
    ("created_at", pa.timestamp("us")),
])


def check_server():
    try:
        r = requests.get(HEALTH, timeout=5)
        if r.status_code == 200 and r.json().get("status") == "ok":
            return
        sys.exit(f"embedding server at {HEALTH} not healthy: {r.status_code} {r.text}")
    except requests.RequestException as e:
        sys.exit(f"cannot reach embedding server at {HEALTH}: {e}\n"
                 f"start it OUTSIDE the sandbox with bin/serve-embed.sh")


def load_inputs():
    """Unique (input_text, sha) from books.duckdb, dedup on input_text."""
    con = duckdb.connect(BOOKS_DB, read_only=True)
    rows = con.execute(INPUT_QUERY).fetchall()
    con.close()
    seen = {}
    for _work_key, text in rows:
        if text not in seen:
            seen[text] = hashlib.sha256(text.encode()).hexdigest()
    return seen  # {input_text: sha}


def load_cached_shas():
    glob = f"{CACHE_DIR}/*.parquet"
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT DISTINCT input_sha256 FROM '{glob}' WHERE model_id = ?",
            [MODEL_ID],
        ).fetchall()
        return {r[0] for r in rows}
    except duckdb.IOException:
        return set()  # no parquet files yet
    finally:
        con.close()


def embed_batch(texts):
    r = requests.post(URL, json={"input": texts, "model": "q"}, timeout=300)
    r.raise_for_status()
    data = r.json()["data"]
    # keep server order via index
    data.sort(key=lambda d: d["index"])
    vecs = np.array([d["embedding"] for d in data], dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def flush(buf, part_idx):
    if not buf:
        return part_idx
    now = datetime.now(timezone.utc)
    table = pa.table({
        "input_text": [b[0] for b in buf],
        "input_sha256": [b[1] for b in buf],
        "model_id": [MODEL_ID] * len(buf),
        "vec": pa.array([b[2].tolist() for b in buf], type=pa.list_(pa.float32(), DIM)),
        "created_at": [now] * len(buf),
    }, schema=SCHEMA)
    path = f"{CACHE_DIR}/part-{now.strftime('%Y%m%dT%H%M%S')}-{part_idx:05d}.parquet"
    pq.write_table(table, path)
    print(f"  flushed {len(buf)} vecs -> {path}")
    return part_idx + 1


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    check_server()

    inputs = load_inputs()
    cached = load_cached_shas()
    misses = [(t, s) for t, s in inputs.items() if s not in cached]

    print(f"inputs: {len(inputs)} unique | cached: {len(cached)} | to embed: {len(misses)}")
    if not misses:
        print("nothing to do — all cached.")
        return

    buf = []
    part_idx = 0
    done = 0
    start = time.time()
    for i in range(0, len(misses), BATCH):
        chunk = misses[i:i + BATCH]
        vecs = embed_batch([t for t, _ in chunk])
        for (text, sha), vec in zip(chunk, vecs):
            buf.append((text, sha, vec))
        done += len(chunk)
        if len(buf) >= FLUSH_EVERY:
            part_idx = flush(buf, part_idx)
            buf = []
        rate = done / (time.time() - start)
        print(f"\r  {done}/{len(misses)}  {rate:.1f} emb/s", end="", flush=True)
    print()
    flush(buf, part_idx)
    print(f"done in {time.time() - start:.0f}s")


if __name__ == "__main__":
    main()
