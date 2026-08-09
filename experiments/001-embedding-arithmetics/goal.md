# Handover: Embedding Arithmetic Experiment

## Goal

Determine whether vector arithmetic on embeddings lets us isolate a *concept* from a *contextually-rich input string*.

For example, we can embed `Movie|1995|Hackers` and get a vector that points at the film *Hackers*, but the input also carries the signals `Movie` and `1995`. We want to know if we can subtract the vectors for those extra signals (`Movie`, `1995`) to obtain a "purer" Hackers concept vector. If it works, that purified vector should be comparable across domains — e.g., it should be close to books like *Snow Crash* or *Neuromancer*, not just close to other 1995 movies.

More generally, we want to answer: **can we use domain/type markers to steer the embedding model toward the right concept, and then strip those markers back out with vector subtraction?**

## What success means

This experiment is intentionally treated as a **vector-space measurement problem**, not a subjective recommendation-quality problem. Success is defined by deterministic, reproducible cosine-similarity scores:

- **Classic analogies:** `cosine(king - man + woman, queen)` should be the highest among a small set of candidates, or at least very high (e.g., > 0.7). This is the positive control.
- **Cross-modal subtraction:** `cosine(purified_vec, target_book_vec)` should be higher than `cosine(unpurified_vec, target_book_vec)`, where the target book is a known thematic neighbor of the concept (e.g., *Neuromancer* or *Snow Crash* for *Hackers*).
- **Input-format comparison:** For each franchise, we compare the top-N book neighbors of each input format and report overlap, rank, and cosine scores. "Better" means higher similarity to expected thematic neighbors and/or greater overlap with the known-book embedding when one exists.
- **Negative control:** `cosine(neg_control, target_book_vec)` should be lower than the genuine purified vector, and the top-N neighbor list should diverge from the genuine results.
- **Type-marker geometry:** Markers should cluster by role (e.g., years with years, media types with media types) with cosine similarities that are meaningfully higher than cross-category pairs.

If subtraction does not improve scores, that is a valid and useful result. The goal is evidence, not magic.

## Questions we want answered

1. **Classic analogy baseline.** Does the famous `king - man + woman ≈ queen` relationship hold in our exact embedding model (Qwen3-Embedding-8B)? This is a positive control.
2. **How extensible is the idea?** If classic analogies work, how far can we push simple vector arithmetic? Does it work for medium/type markers (`Movie`, `Book`, `Game`), years, genres, themes?
3. **Domain accuracy.** For our actual use case — book recommendations from non-book preferences — what input formats and arithmetic (if any) move the query vector measurably closer to known thematic book neighbors?

## Starting experiments

These are suggested starting points. Iterate as you learn. Add, remove, or change examples based on what the early results show.

### Experiment 1: Classic analogy positive control

Embed these strings:

```text
king
man
woman
queen
```

Compute `king - man + woman`, L2-normalize the result, and rank all four embedded strings by cosine similarity to that computed vector. Check whether `queen` is the nearest neighbor and report the score.

Also try a few other classic relations if you want:

```text
france, paris, italy, rome
dog, puppy, cat, kitten
walk, walked, run, ran
```

Success metric: `queen` (or the analogous expected word) should be rank 1 and have the highest cosine similarity. Report top-3 candidates and their scores.

### Experiment 2: Cross-modal subtraction

Embed these strings:

```text
Hackers
Hackers (film)
The 1995 film Hackers
Movie|Hackers
Movie|1995|Hackers
Movie
1995
```

Compute the "purified" vector:

```text
normalized( vec(Movie|1995|Hackers) - vec(Movie) - vec(1995) )
```

For each of these query vectors — `purified`, `Hackers`, `Hackers (film)`, `The 1995 film Hackers`, `Movie|Hackers`, `Movie|1995|Hackers` — find the top 10 nearest books in the corpus using `list_cosine_similarity`.

Compare the result lists numerically: overlap at top-5 and top-10, cosine scores to a small set of expected cyberpunk/hacking books (e.g., *Neuromancer*, *Snow Crash*), and whether the purified vector scores higher or lower than the simpler inputs.

### Experiment 3: Multiple cross-media examples

Pick a small set of well-known works that exist across media or have obvious thematic neighbors in books:

```text
Blade Runner
Movie|Blade Runner
Movie|1982|Blade Runner
Book|Philip K. Dick|Do Androids Dream of Electric Sheep?

The Witcher
Game|The Witcher
Book|Andrzej Sapkowski|The Witcher

Dune
Movie|Dune
Movie|2021|Dune
Book|Frank Herbert|Dune

Halo
Game|Halo
Book|William C. Dietz|Halo: The Flood

The Lord of the Rings
Movie|The Lord of the Rings
Book|J.R.R. Tolkien|The Lord of the Rings
```

For each franchise, compute cosine-similarity-based book neighbors for:

- Raw title (`Blade Runner`)
- Type-prefixed (`Movie|Blade Runner`)
- Type + year (`Movie|1982|Blade Runner`)
- Subtracted purified vector (`Movie|1982|Blade Runner - Movie - 1982`)
- The corresponding book embedding (where it exists)

Report top-5 neighbors for each, plus the score to the known-book embedding when available. Determine which input format moves the vector closest to the known-book embedding and to expected thematic neighbors.

### Experiment 4: Negative control

Compute:

```text
normalized( vec(Movie|1995|Hackers) - vec(Pineapple) - vec(Tuesday) )
```

Find its nearest books. If arbitrary subtraction produces scores and neighbor lists comparable to the genuine purified vector, the method is not isolating anything meaningful. This is a sanity check.

Optionally also test subtracting random unit vectors, or subtracting `Movie` and `1995` from an unrelated title.

### Experiment 5: Type-only markers

Embed domain/type markers on their own and see how they sit in the space:

```text
Movie
Book
Game
Director
Genre
Theme
Event
1995
1982
2021
```

Compute pairwise cosine similarities. Do these markers cluster together by role? Are years closer to each other than to media types? Understanding their geometry helps interpret whether subtracting them is meaningful.

## Setup and prerequisites

The embedding server must be running **outside the sandbox** before the experiment starts.

### 1. Start the embedding server

Ask the user (Jack) to run:

```bash
bin/serve-embed.sh
```

This starts llama-server on `http://localhost:8080` with the Qwen3-Embedding-8B model.

### 2. Verify the server is healthy

From inside the sandbox, check the health endpoint:

```bash
curl http://localhost:8080/health
```

Expected response:

```json
{"status":"ok"}
```

Do not proceed until you see that response.

### 3. Embed strings

Use the same endpoint and normalization that `bin/embed.py` uses. A minimal Python helper:

```python
import requests
import numpy as np

def embed(texts):
    r = requests.post("http://localhost:8080/v1/embeddings",
                      json={"input": texts, "model": "q"},
                      timeout=300)
    r.raise_for_status()
    data = r.json()["data"]
    data.sort(key=lambda d: d["index"])
    vecs = np.array([d["embedding"] for d in data], dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms
```

Use `numpy` only for vector arithmetic and normalization. **Do not rely on pandas or polars** — they are not in the flake.

### 4. Query against the book corpus with DuckDB

The book corpus lives in `data/embeddings/*.parquet` and its metadata in `data/books.duckdb`. Vectors are L2-normalized, so `list_cosine_similarity` equals dot product.

The easiest path is to write your computed query vectors to a temporary Parquet file and query them with DuckDB. Example:

```python
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import numpy as np

# Assume query_vec is a 4096-d float32 L2-normalized vector.
vec = np.asarray(query_vec, dtype=np.float32)
table = pa.table({
    "label": ["purified_hackers"],
    "vec": pa.array([vec.tolist()], type=pa.list_(pa.float32(), 4096)),
})
pq.write_table(table, "/tmp/query_vec.parquet")
```

Then run nearest-neighbor search in DuckDB:

```sql
ATTACH IF NOT EXISTS 'data/books.duckdb' AS books_db (READ_ONLY);

CREATE OR REPLACE VIEW book_meta AS
SELECT 'Book|' || a.name || '|' || b.title AS input_text,
       a.name AS author,
       b.title,
       b.first_publish_year AS year,
       b.edition_count AS editions
FROM books_db.books b
JOIN books_db.authors a ON a.author_key = b.primary_author_key
QUALIFY row_number() OVER (
  PARTITION BY 'Book|' || a.name || '|' || b.title
  ORDER BY b.edition_count DESC, b.first_publish_year) = 1;

CREATE OR REPLACE VIEW book_vecs AS
SELECT m.*, e.vec
FROM book_meta m
JOIN 'data/embeddings/*.parquet' e ON e.input_text = m.input_text;

-- top 10 books nearest to the query vector
SELECT author, title, year, editions,
       round(list_cosine_similarity(bv.vec, q.vec), 4) AS sim
FROM book_vecs bv
CROSS JOIN read_parquet('/tmp/query_vec.parquet') q
ORDER BY sim DESC
LIMIT 10;
```

Alternatively, start an interactive DuckDB session with the helper views already loaded:

```bash
duckdb -init sql/similar.sql
```

This gives you the `book_vecs`, `find_book`, `similar_to`, and `recommend` macros defined in `sql/similar.sql`.

## Useful facts

- Model: **Qwen3-Embedding-8B-Q8_0**, pooling = `last`, output = **4096-dim float32**.
- Vectors are **L2-normalized client-side** in the existing pipeline. When comparing, cosine similarity == dot product.
- Book input format: `Book|${author}|${title}`.
- The corpus currently contains ~27.5k embedded books.
- Brute-force cosine over the corpus is fast enough for experiments — no ANN needed.
- **Tool constraint:** use DuckDB for all corpus querying and pyarrow/numpy for vector work. pandas/polars are not in the flake.

## Output format

Produce a single Markdown document. Keep it readable for a non-specialist developer but include enough detail that the results can be reproduced or challenged.

Suggested structure:

```markdown
# Embedding Arithmetic Experiment: Results

## Executive summary
- 2–3 sentences on what you found overall.
- Is vector subtraction a viable way to strip domain markers?

## Hypotheses and findings

### 1. Classic analogies
- Cosine scores and ranks for `king - man + woman` against king/man/woman/queen.
- Same for any other analogy tested.

### 2. Cross-modal subtraction
- For each test case (Hackers, Blade Runner, etc.), show:
  - top-5 book neighbors for each input format
  - cosine score to the known-book embedding when one exists
  - overlap between neighbor lists
- State whether subtraction raised, lowered, or did not change similarity scores.

### 3. Negative controls
- Cosine scores and top neighbors for arbitrary subtraction.
- Did the control diverge from the genuine purified vector?

### 4. Type-marker geometry
- Pairwise cosine matrix for `Movie`, `Book`, `Game`, `1995`, `1982`, `2021`, etc.
- Brief note on whether markers cluster by role.

## Recommendations
- Which input format should we use for non-book preferences?
- Should we pursue vector subtraction at all?
- Any input formats that scored surprisingly well or badly?

## Appendix: test strings, vectors, and queries
- Full list of embedded strings.
- Python/DuckDB snippets used.
- Raw result tables if needed.
```

Include exact input strings, cosine similarities, and top neighbor lists. Do not hand-wave — the goal is to ground the decision in evidence from our exact model.

## What success looks like

We are not expecting subtraction to magically work. The most useful outcome is a clear, evidence-based answer to:

> For non-book preferences, what is the best way to form an input string so that its embedding points at the concept we want, and how much can vector arithmetic improve that?

If subtraction is useless, say so. If a simple input format like `Movie|Blade Runner` or `Blade Runner (film)` is good enough, that is also a valuable result.
