# gonum capabilities for a Go vector-recommendation service

Research note scoped to: **can gonum replace the math DuckDB does today
(`list_cosine_similarity` over a 27.5k×4096 corpus) and the planned k‑means
clustering step, while staying pure-Go and dropping DuckDB?**

Corpus fact sheet (from `docs/dev/embeddings.md`, `docs/dev/web-backend.md`):

- ~27,500 books, 4096‑dim **float32**, L2‑normalized, ~449 MB on disk.
- Today read from `data/embeddings/part-*.parquet`
  (`fixed_size_list<float32,4096>`) by embedded DuckDB (`go-duckdb`, CGO),
  which does cosine == dot natively.
- Planned: k‑means clustering of the corpus for cluster‑style recommendations
  (see `ideas/clustered-recommendations.md`, `ideas/preference-clusters.md`).

gonum at time of writing: module `gonum.org/v1/gonum`, latest **v0.17.0**
(Dec 2025), Go 1.24, BSD‑3. "Pure Go with some assembly."

---

## 1. gonum inventory

All packages under `gonum.org/v1/gonum/...`. "Usable here?" is judged against
the 4096‑dim float32 corpus + recommend/clustering workload.

| Package | Role | Usable here? |
|---|---|---|
| `mat` | Dense/SymDense/VecDense matrices, factorizations (QR/LU/Cholesky/SVD/Eigen), Mul, Norm, Solve. **float64 + complex128 only.** | **Partial.** Mul/Norm/dot work but force f64. No f32 matrices. |
| `blas` | BLAS interfaces + `blas64`/`blas32`/`cblas*` wrappers + pure‑Go `blas/gonum` impl. Swap in a C BLAS via `blas64.Use(...)`. Level1 has `Dot`, `Nrm2`, `Iamax` (argmax); Level2 `Gemv`, Level3 `Gemm`. | **Yes for the math**, but `blas32` is a thin raw API; `mat` won't drive it. f32 Gemv/Gemm available if you drop down to `blas32`. |
| `blas/blas32` | float32 BLAS wrapper (Dot, Nrm2, Gemv, Gemm, Iamax, …). | **Yes** — this is gonum's only real f32 linear‑algebra surface. |
| `lapack` / `lapack/gonum` / `lapack64` | LAPACK (factorizations, eigen, least squares). float64/complex. | Not needed for centroid/dot/top‑k. Skip. |
| `floats` | `[]float64` helpers: **`Dot`, `Norm`, `Distance`, `Argsort`, `ArgsortStable`, `Sum`, `Scale`, `Add`, `Max`, `MaxIdx`**, no alloc. | **Yes** for top‑k/argsort, but f64 only. |
| `floats/scalar` | scalar f64 helpers. | Not relevant. |
| `cmplxs` | complex128 slice helpers. | No. |
| `stat` | Descriptive/univariate stats: Mean, Variance, StdDev, Correlation, Covariance, Histogram, ROC/TOC, `LinearRegression`, and types **`PC`** (PCA) and **`CC`** (canonical correlation). **No k‑means. No silhouette. No clustering.** | **PCA** could be a future dimensionality step. Otherwise not needed. |
| `stat/distuv`, `stat/distmv`, `stat/distmat` | Probability distributions (Normal, StudentsT, MvNormal, Wishart…). | Future Gaussian/cluster‑model work maybe; not now. |
| `stat/samplemv`, `stat/sampleuv` | Advanced samplers (MH, importance). | Future. |
| `stat/mds` | Multidimensional scaling (embed distance matrices). | Not now. |
| `stat/combin` | Combinatorics (permutations, combinations). | Utility. |
| `stat/card`, `stat/spatial` | Cardinality estimation, spatial stats. | No. |
| `spatial/kdtree` | k‑d tree with `Comparable`/`Interface` (float64, Euclidean). `Nearest` with a `Keeper` heap. | **No for 4096‑d.** k‑d trees don't prune in high dimensions — degrades to brute force. Also f64. |
| `spatial/vptree` | VP‑tree, `Comparable.Distance`, float64. NN with a `Keeper` heap. | **No for 4096‑d** (same curse; works best in low‑d / metric. Actually fine for *centroid* clustering where k is small, over *centroids* (k≪n) — see §3). |
| `spatial/barneshut` | n‑body force approximation. | No. |
| `spatial/r2`, `r3`, `transform`, `curve` | 2D/3D vectors, transforms, space‑filling curves. | No. |
| `optimize` | Nonlinear optimization (gradient methods, Nelder‑Mead, BFGS, …). | Could fit a custom model later (e.g. weighted centroid reg). Not needed for plain k‑means/NN. |
| `optimize/convex/lp` | Linear programming. | No. |
| `diff/fd` | Finite‑difference derivatives. | No. |
| `integrate` / `integrate/quad` | Quadrature. | No. |
| `interp` | 1D interpolation. | No. |
| `dsp/*` | Fourier/window/transform (DSP). | No. |
| `graph/*` | Graph types, traversal, pathfinding, **community detection** (`graph/community`), spectral (`graph/spectral`), layout. | Tangential. Community detection on a *book‑similarity graph* is a legit alt‑clustering route, but that's a new design, not a swap‑in. |
| `num/*` | Dual/hyperdual/quaternion dual numbers. | No. |
| `mathext` | Special math funcs (erf, gamma…). | Utility. |
| `unit` | SI units. | No. |

### What is conspicuously **absent** from gonum

- No `kmeans` or any partitional/hierarchical clustering package in‑tree.
- No silhouette score or any internal cluster‑validation metric.
- No top‑k / argmax‑over‑matrix / `list_cosine_similarity`‑equivalent — you
  roll it from `floats.Argsort` + `blas32.Gemv`/`Dot` (or a partial‑sort heap).
- No float32 in `mat`; `blas32` is the only f32 surface and the maintainers
  have said (issue #1583, gonum‑dev thread) f32 `mat` is **"non‑trivial, not
  likely soon."**
- No Parquet/Arrow/columnar I/O. No SQL, no state store, no HTTP. gonum is a
  *math* library; plumbing is yours.

---

## 2. What gonum covers (for this workload)

If you accept working in **float64 + raw BLAS**, gonum covers:

1. **Centroid matmul / dot products.** Store the corpus as a `mat.Dense`
   (27,500 × 4096, row‑major) or as a raw `blas64.General`. The preference
   centroid (4096‑vec) × corpus^T is one **`Gemv`** (Level‑2 BLAS, ~112 MFLOP)
   — trivial, sub‑millisecond even in pure‑Go BLAS. Cosine == dot since vectors
   are L2‑normalized.
   - `mat.Dense.MulVec(a.T(), centroid)` does exactly this and dispatches to
     `blas64.Gemv` when `a` is a `RawMatrixer`. Same path DuckDB's
     `list_cosine_similarity` takes internally.
2. **BLAS backing is real and swappable.** Default is pure‑Go `blas/gonum`
   (with amd64/arm64 assembly for hot loops). You can register a C BLAS via
   `gonum.org/v1/netlib/blas` (`blas64.Use(netlib.Implementation{})`) which
   links OpenBLAS/MKL/Apple Accelerate through CGO. For a 27.5k‑row Gemv this
   is overkill, but it means a 75k or 750k corpus stays comfortable.
3. **L2 normalization.** `floats.Norm(vec, 2)` then `floats.Scale(1/norm, vec)`
   — but for f64. For f32 you'd use `blas32.Nrm2` + `blas32.Scal`.
4. **Top‑k / argsort.** `floats.Argsort(scores, idx)` sorts all 27.5k and
   returns indices. For top‑20 this is ~25k more work than a bounded heap, but
   at this scale it's microseconds and dead simple. A real top‑k helper isn't
   in gonum; `spatial/kdtree.Keeper`/`vptree.Keeper` give you a max‑heap keeper
   you could reuse, but the trees themselves don't help at 4096‑d.
5. **PCA / whitening** (`stat.PC`) if you ever want to compress 4096→256 to
   make HNSW/clustering cheaper — a plausible future direction and the one
   place gonum adds something DuckDB doesn't.
6. **KD/VP‑tree NN *over centroids*** — for the future "which cluster is this
   user in" lookup (k ≈ 10–256, low‑ish, but still 4096‑d so still dubious).
   Realistic only after PCA.

### The catch: float32 vs float64

The cache stores `float32`. To use `mat`/`floats` you must widen to `float64`,
which **doubles resident RAM**: 27,500 × 4096 × 8 = **~901 MB** (vs ~450 MB
f32). For a single‑user demo that's fine; if you want to hold the 75k tier
(`>=5`) or 757k catalog in memory it stops being fine. Casting cost itself is
one O(n·d) pass at load (~1.1 GFLOP of memory traffic) — a few hundred ms, i.e.
amortized away at startup, irrelevant per‑request.

`blas32` would let you keep f32 in memory and still get `Dot`/`Gemv`/`Nrm2`/
`Iamax` with assembly, but you give up `mat`'s ergonomic `Dense`/`MulVec` and
write against the raw BLAS C‑like API (`blas32.Gemv(...) with Stride,
transposes`, manage the `blas32.General` yourself). That's ~50 lines and
honestly the right call if RAM matters.

---

## 3. What gonum does NOT do, and Go‑ecosystem substitutes

| Gap | Substitute (Go) | Notes |
|---|---|---|
| **Parquet I/O** (`fixed_size_list<float32,4096>`) | **`github.com/apache/arrow/go/v17`** (`parquet/file`, `parquet/pqarrow`). | Confirmed exists and supports the schema: the leaf is a `Float32ColumnChunkReader` (`parquet/file.Float32ColumnChunkReader.ReadBatch`), and the `fixed_size_list` wrapper is handled by `pqarrow` reading into an Arrow `FixedSizeList` array. So you can stream `vec` straight into `[]float32` with zero widening. This is the real drop‑in for DuckDB's parquet reader. A thinner alternative is `github.com/parquet-go/parquet-go` (pure Go, no Arrow dep), also fine for `fixed_size_list<float32,N>` via a generated/reflect node + repeated float32 column; `apache/arrow/go` is the more battle‑tested reader and matches the cache's Arrow‑ish schema exactly. |
| **f32 SIMD dot / top‑k** | `blas32` (in‑tree) is enough; for real SIMD consider **`github.com/klauspost/cpuid`+asm** or a vendor lib. But honestly 27.5k×4096 dot is GPU‑free territory — a single `blas32.Gemv` is sub‑ms. Don't over‑engineer. | Tuned vector top‑k (partial heaps, SIMD‑argmax) is *not* a gonum feature and *not* needed at this scale. |
| **k‑means / clustering** | No gonum package. Options: write it yourself (~80 LoC: init++ or random, assignment via `Gemv`, update = row means, 10–20 iterations over 27.5k×4096 ≈ seconds). Or use a third‑party lib: **`github.com/e-XpertSolutions/go-cluster/v2`** (k‑modes/k‑prototypes — wrong distance for us), **`github.com/muesli/kmeans`** (clean, pure‑Go, k‑means++ with custom distance funcs incl. cosine, Clusterer interface — well‑fit), or `github.com/lanrat/kmeans` (similar). | For cosine on unit vectors, k‑means *using Euclidean* is equivalent to spherical k‑means up to a monotone transform (‖x−c‖² = 2−2·x·c on the unit sphere), so default `muesli/kmeans` with Euclidean distance over **L2‑normalized** vectors ≈ maximizing cosine to the centroid. Re‑normalize centroids each step (spherical k‑means). |
| **Silhouette / cluster‑validation** | Not in gonum. Roll your own (~30 LoC: for each point, a = mean intra‑cluster dist, b = min mean inter‑cluster dist, s = (b−a)/max(a,b)). `muesli/kmeans` exposes assignments so you only supply a distance. | The "choose k" step (`ideas/preference-clusters.md`) needs this; it's a half‑page function, not a library decision. |
| **Approximate nearest‑neighbor index** (future, when 27.5k brute force feels slow — really only after the 75k/750k scale‑up) | Pure Go: **`github.com/coder/hnsw`** (generics, f32, in‑mem, minimal, well‑maintained), **`github.com/nijaru/hnsw-go`** (f32, mmap persistence, filtering, segmented — closest to a real "vector DB in process"), **`github.com/habedi/hann`** (HNSW + PQIVF + RPT, f32). CGO: `github.com/oligo/hnswgo` (binds nmslib hnswlib), `github.com/Bithack/go-hnsw`. | At 27.5k × 4096 brute‑force `Gemv` is already <1 ms — ANN is premature. Reach for `coder/hnsw` or `hnsw-go` when you cross ~100k vectors or want sub‑linear query with filters. |
| **State store** (users, ratings, on‑demand vectors) | DuckDB's role as `app.duckdb` → swap to **SQLite** (`modernc.org/sqlite` pure‑Go, or `mattn/go-sqlite3` CGO) or **BBolt** (`go.etcd.io/bbolt`) for a single‑file KV. `on demand_vecs` is a tiny table — either works. | Pure‑Go SQLite (`modernc.org/sqlite`) keeps the "no CGO" property of a gonum path; if you keep `go-duckdb` you've kept CGO anyway. |
| **HTTP / JSON** | stdlib `net/http` + `encoding/json` (already used). gonum contributes nothing here and shouldn't. | — |

---

## 4. Verdict

**For the math DuckDB does *today*, gonum is sufficient — but only at the cost
of either (a) widening f32→f64 (2× RAM, use `mat`/`floats` ergonomically) or
(b) staying f32 and writing against `blas32` directly (no `mat`, ~50 LoC of
raw BLAS). The single hot operation — centroid × corpus^T — is a Level‑2
`Gemv` that pure‑Go BLAS does in well under a millisecond at 27.5k rows. You
will not measure a difference versus DuckDB's `list_cosine_similarity` for
this. Top‑k is `floats.Argsort` (or a 20‑line bounded heap) — again, gonum‑
free territory; you'd hand‑roll it the same way regardless.**

**Where the Go‑only architecture diverges from the DuckDB architecture is on
*plumbing and growth surface*, not core math:**

- **Parquet**: DuckDB *is* your Parquet reader today. Dropping it means
  adopting `apache/arrow/go`'s `parquet` reader (or `parquet-go`) to load the
  `fixed_size_list<float32,4096>` cache at startup. This works — Arrow's
  `Float32ColumnChunkReader` + `pqarrow` reads that schema directly into
  `[]float32` — and removes the CGO/duckdb dependency. It's the load‑bearing
  substitution; everything else is optional.
- **k‑means** (the planned cluster step): gonum gives you **nothing** in‑tree.
  You either hand‑roll spherical k‑means over `blas32`/`mat` (trivial and
  fast) or pull `muesli/kmeans`. Silhouette for choosing k is a hand‑roll
  either way. DuckDB wouldn't have helped here either — it has no k‑means —
  so this dimension is **Go‑only either way**, and gonum's missing clustering
  package is not a reason to stay on DuckDB.
- **Future algorithms**: the strongest *unique* argument for keeping gonum in
  the stack is `stat.PC` (PCA) for compressing 4096→256 ahead of HNSW/clustering,
  and `optimize` if you ever fit a learned scoring model. DuckDB has neither.
  So a Go‑only path actually *widens* your algorithmic options over time
  (PCA, optimization, graph community detection via `graph/community`), while
  the DuckDB path is locked to "SQL over columns + list similarity."
- **Perf tuning**: the lever gonum uniquely offers is **swappable C BLAS**
  (`gonum.org/v1/netlib/blas` → OpenBLAS/Accelerate). At 27.5k you won't need
  it; at 75k–757k scale‑up it's how the same Go code stays fast without a
  rewrite. DuckDB has its own vectorized engine but you can't tune it per‑op.
  The counter‑lever DuckDB has that Go‑only loses: SQL‑level query planning,
  columnar scans, and `list_cosine_similarity` being one optimized builtin.
  You're trading "pushdown to a columnar engine" for "own the hot loop."

**Bottom line:** gonum can replace the math — comfortably, for the current
shape, in a few hundred lines, with `blas32` (keep f32) or `mat` (widen to
f64). It **cannot** replace the Parquet I/O or the app DB; those move to
`apache/arrow/go` + SQLite/BBolt. It **does not** provide k‑means/silhouette,
but neither does DuckDB, so that's a push. The Go‑only architecture is a net
win on *future flexibility* (PCA, ANN libs, swappable BLAS, custom scoring)
and a net wash on *current perf*; its only real costs are (1) writing the
loader and (2) giving up DuckDB's SQL ergonomics for ad‑hoc exploration. If
you're committed to clustering + iterating on recommendation algorithms, Go‑
only with gonum+arrow+sqlite is the more演进‑friendly bet; if "give me JSON
over my parquet with zero code" stays the priority, DuckDB keeps earning its
CGO.

### Recommended concrete stack (if you go Go‑only)

- `github.com/apache/arrow/go/v17/parquet` + `pqarrow` — load
  `fixed_size_list<float32,4096>` into `[]float32` once at boot.
- `gonum.org/v1/gonum/blas/blas32` for the centroid `Gemv`/`Dot` (stay f32,
  keep corpus at ~450 MB). `gonum/floats` if you accept f64 widening.
- `github.com/muesli/kmeans` (or hand‑roll spherical k‑means on `blas32`) +
  a 30‑LoC silhouette for choosing k.
- `modernc.org/sqlite` (pure‑Go) or `bbolt` for users/ratings/ondemand_vecs.
- `github.com/coder/hnsw` (or `nijaru/hnsw-go` if you want mmap persistence)
  — **only** when the corpus grows past ~100k; defer for now.
- Optionally `stat.PC` (gonum) for a 4096→~256 PCA pre‑pass before clustering
  / HNSW — the one place gonum genuinely out‑performs what DuckDB offers.

### Sources

- gonum suite / package tree: `https://godocs.io/gonum.org/v1/gonum`
  (v0.17.0 directories listing).
- `mat` doc & BLAS/LAPACK design:
  `https://github.com/gonum/gonum/blob/v0.17.0/mat/doc.go`.
- `mat` is float64‑only; f32 `mat` explicitly non‑trivial / not planned:
  gonum/gonum issue #1583 and gonum‑dev "Gonum for types other than float64"
  thread.
- `stat` index (no kmeans/silhouette; has `PC`, `CC`):
  `https://godocs.io/gonum.org/v1/gonum/stat`.
- `floats` index (`Dot`, `Norm`, `Argsort`, `Distance`…):
  `https://godocs.io/gonum.org/v1/gonum/floats`.
- `blas32` float32 BLAS wrapper: `gonum.org/v1/gonum/blas/blas32`.
- `spatial/kdtree`, `spatial/vptree` `Comparable` interfaces (float64,
  Euclidean): `gonum/gonum` master.
- Apache Arrow Go Parquet reader + `Float32ColumnChunkReader` + `pqarrow`:
  `https://pkg.go.dev/github.com/apache/arrow/go/v17/parquet/file`,
  SO answer pointing to `pqarrow` for repeated/list fields.
- HNSW in Go: `github.com/coder/hnsw` (pure Go, f32, generics),
  `github.com/nijaru/hnsw-go` (persistence+filtering),
  `github.com/habedi/hann` (HNSW/PQIVF/RPT),
  `github.com/oligo/hnswgo` (CGO bind of hnswlib).
- k‑means in Go: `github.com/muesli/kmeans` (k‑means++, pluggable distance),
  `github.com/lanrat/kmeans`.