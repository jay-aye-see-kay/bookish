# Outcome: Embedding Arithmetic Experiment

## Goal (summary)

Determine whether vector arithmetic on embeddings can isolate a concept from a
domain/type-marked input string (e.g. `Movie|1995|Hackers`), and find the best
input format for embedding non-book preferences so they retrieve relevant books.

## What we found

### 1. Vector subtraction does not strip markers

Every subtraction attempt — `Movie|1995|Hackers - Movie - 1995`, using bare
markers, explicit markers (`category: Movie`, `year: 1995`), pipe syntax,
JSON, INI, YAML, or sentences — produced worse results than the marked input
itself. In most cases scores flipped negative and top neighbours became random
unrelated books. In a few cases where the title vector was very dominant
(`1984`, `Halo`) some signal survived, but scores still dropped sharply.

### 2. Domain markers do disambiguate

The marked input itself is the useful representation:

| Input | Retrieval result |
|---|---|
| `Hackers` | computer-security manuals |
| `The 1995 movie Hackers` | *Snow Crash*, *Neuromancer*, *Ready Player One* |
| `Halo` | YA novel *Halo*, *Minecraft* |
| `category: Game\ntitle: Halo` | *Halo Cryptum*, Halo novels |
| `1984` | mixed results, *Nineteen Eighty-Four* at 0.6985 |
| `Book|1984` | *Nineteen Eighty-Four* at 0.8848 |

### 3. Input-format ranking

From best to worst retrieval quality across the test concepts:

1. **Parenthetical** — `Title (movie)` / `Title (book)` — most consistent.
2. **Natural sentence** — `The 1995 movie Hackers` — strong but less cache-friendly.
3. **Pipe** — `Movie|Title` — excellent for highly ambiguous titles.
4. **Colon/newline and YAML** — decent, comparable to pipe.
5. **JSON** — decent but never best; syntactic overhead.
6. **INI** — weakest marked format.

Release years (`Movie|1995|Hackers` vs `Movie|Hackers`) did not reliably help
and sometimes hurt.

### 4. Why subtraction failed

Explicit markers (`category: Movie`, `year: 1995`) are not embedding-space
distant from bare markers (`Movie`, `1995`) — cosine similarities are ~0.88–0.95.
So disambiguating the subtrahend does not change the geometry. The embedding
model processes a marked string as a single contextualized representation, not
as a sum of independent `marker + title` vectors, so subtracting a marker vector
cannot cleanly remove it.

## Decisions

- **Do not use vector subtraction** to remove domain markers.
- **Keep domain markers in the input string.** They are valuable for
disambiguation and thematic steering.
- **Default input format:** `Title (type)` (parenthetical). It is simple,
cache-friendly, and performed most consistently.
- **Fallback for highly ambiguous titles:** `Type|Title` (pipe), e.g.
`Book|1984`.
- **Do not include release years** in the default format.
- **Cache embeddings per exact input string**, because format changes produce
meaningfully different vectors.

## Open questions

- How do these formats perform on genuinely ambiguous short titles (`It`,
  `Cars`, `Up`, `Contact`) in a larger shootout?
- Would a learned linear projection (trained on known cross-domain pairs)
  succeed where naive subtraction fails?
- Does unnormalized subtraction preserve useful magnitude information that
  cosine search discards?

## Artifacts

- `goal.md` — original experimental plan.
- `iteration-01-initial-subtraction.md` — first results (classic analogies,
  cross-modal subtraction, negative controls, marker geometry).
- `iteration-02-syntax-formats.md` — second iteration testing multiple input
  syntaxes and explicit marker subtrahends.
- `scripts/` — Python runners for both iterations.
- `data/` — generated query vectors and intermediate Parquet files.
