# Clustered recommendations

## Problem

`recommend_from_file` (in `sql/similar.sql`) ranks every candidate by the
**average** cosine to all liked books:

```sql
round(avg(list_cosine_similarity(bv.vec, h.vec)), 3) AS sim
```

Ranking by mean cosine == ranking by cosine to the **centroid** — one averaged
"taste vector". That collapses a multi-modal taste (e.g. hard SF + thrillers +
fantasy) into a single mushy point between the clusters. Consequences:

- The **densest** cluster wins. Today `preferences.txt` is mostly SF, so recs
  are ~all SF even though the list also has Lee Child (thrillers) and Narnia
  (fantasy).
- A genuinely great thriller rec scores *low* because it's far from every SF
  book, dragging its average down — even if it's a perfect match for the
  thriller side.

This gets worse as more (and more varied) preferences are added.

## Goal

Recommendations that respect distinct taste clusters, so each side of the taste
gets represented instead of being averaged away.

---

## Option A — "nearest liked book" (MAX). Pure SQL, no clustering.

Rank each candidate by similarity to its **single closest** liked book:

```sql
max(list_cosine_similarity(bv.vec, h.vec)) AS sim
```

- A thriller candidate scores high off Lee Child; no longer punished for being
  far from the SF pile. **Implicitly** respects clusters without computing them.
- One-line change → add as a new macro (don't clobber the mean version; keep
  both for comparison). Suggested name: `recommend_from_file_nn` or a
  `strategy := 'max'|'mean'` variant.
- Covers ~80% of the value with zero new machinery.

**Tradeoffs**

- No label — output doesn't say *which* taste a rec came from.
- Can over-reward books sitting right next to one liked title (near-duplicates,
  same-series editions). Mitigate with the existing `exclude_pref_authors` and
  maybe a small floor on how many liked books must be near it.

**Do this first** — it's a strict improvement usable today.

---

## Option B — explicit clusters (labels + N recs per cluster)

Group liked books into k taste-clusters, take one centroid per cluster, return
top-N per centroid tagged with a representative liked book.

Desired output shape:

```
cluster: hard SF (Rendezvous with Rama, Seveneves, 三体…) → Blindsight, Revelation Space…
cluster: thrillers (Killing Floor, Tripwire…)            → Reacher-adjacent…
cluster: classic robots/AI (I, Robot, Murderbot…)        → …
```

**Why a script:** DuckDB has no native k-means. The liked set is tiny (dozens of
vectors), so cluster it in Python (numpy/sklearn) — trivially fast — then let
SQL do per-centroid nearest-neighbour recs.

**Sketch**

1. Read liked vectors: fuzzy-match `preferences.txt` lines → `book_vecs` (reuse
   the `hits` CTE logic already in `recommend_from_file`). Export the matched
   `input_text` + `vec`.
2. Cluster the liked vectors (k-means on L2-normalized vecs == spherical
   k-means; cosine-friendly). Pick k via a small heuristic (elbow / silhouette)
   or expose `k` as a CLI arg. Small-list sanity: cap k so clusters aren't
   singletons.
3. For each cluster: compute centroid (mean of member vecs, re-normalize),
   label it with its nearest member title(s).
4. Recommend per centroid: top-N candidates by cosine to that centroid,
   excluding liked books (and optionally liked authors). Emit
   `(cluster_label, author, title, year, sim)`.
5. Merge/dedupe across clusters (a book may be near two centroids — keep the
   higher sim, or show which cluster claimed it).

**Where it lives**

- Small Python driver under `bin/` (mirrors `bin/embed.py` conventions:
  read-only `books.duckdb`, glob `data/embeddings/*.parquet`).
- Could write centroids/labels back to a temp parquet and finish ranking in
  SQL, or do the whole thing in Python and print a table. SQL-side keeps it
  consistent with the repo's DuckDB-first ethos.

**Open questions**

- How to choose k? Fixed, CLI arg, or auto. Start with a CLI arg.
- Diversity vs. relevance in the merged list (round-robin across clusters vs.
  global sim sort).
- Do we want soft assignment (a book can belong to 2 clusters) for the *liked*
  set? Probably hard assignment is fine at this scale.

---

## Recommended sequencing

1. **Now:** add the MAX macro to `sql/similar.sql`; run it against the current
   `preferences.txt` and diff against the centroid output to sanity-check.
2. **Later:** build the clustered recommender (Option B) once the preference
   list grows and the labeled/grouped view earns its keep.

## Files touched

- `sql/similar.sql` — new macro(s) for Option A.
- `bin/recommend_clustered.py` (new) — Option B driver.
- `docs/dev/comparing-books.md` — document the new recommend strategies.
