# Preference clusters

## Goal

Better recommendations. The current recommender averages a user's liked books
into a single "taste vector" and ranks the whole catalogue by similarity to that
one point. That collapses a multi-modal taste into a mushy middle and lets the
densest cluster win: someone who likes hard SF *and* Jack Reacher thrillers
*and* fantasy gets almost entirely SF back, because a great thriller scores low
— it's far from every SF book, dragging its average down even when it's a
perfect match for the thriller side.

The fix is to recognise that a user's preferences form **distinct clusters
dynamically**, and to generate a separate set of recommendations from each
cluster rather than one averaged set. Concretely, a user's preferences — say,
some Jack Reacher books, some sci-fi, some fantasy — should naturally resolve
into two or three (or more) taste clusters at query time, each of which produces
its own ranked recommendation list.

How those per-cluster lists are then merged back together or displayed to users
(separate clumps, round-robin, a global sort with cluster labels, etc.) is out
of scope for now. The first goal is just to *produce* the clustered
recommendation sets.

## Verification

I took a real account with 29 ratings (SF/Asimov, Octavia Butler, Andy Weir,
Heinlein, Stephenson, Doctorow, Lee Child thrillers, Palahniuk, Bukowski,
Hemingway, and one fantasy title) and clustered its **liked** book vectors with
spherical k-means (vectors are already L2-normalised, so cosine similarity
== dot product; k-means on normalised vectors is spherical k-means). Then I
ranked the full ~27.5k-book corpus against each cluster centroid, and compared
to the current single-centroid baseline. No data was mutated — read-only probe.

The headline result: **the current single-centroid baseline's top-17
recommendations are 100% science fiction.** Hugh Howey's *Wool*, James S.A.
Corey's *Leviathan Wakes*, Nancy Kress, Philip K. Dick, Ann Leckie, Scalzi,
Card, Martha Wells… every one SF. The 11 thriller and literary likes produced
**zero** visible recommendations because they're averaged away.

### k=3 — clean SF / Doctorow / thriller+literary split

The likes fell into three clusters with good cohesion:

- **Cluster A (SF, 14 books):** Octavia Butler, Asimov, Andy Weir, Heinlein,
  Stephenson, Cargill, Islington → recommends *Leviathan Wakes, Ancillary
  Justice, Beggars in Spain, Adulthood Rites, Wool, Earthseed…*
- **Cluster B (Doctorow, 2 books)** → *Little Brother, Walkaway, Down and Out
  in the Magic Kingdom…* — none of which appear in the baseline.
- **Cluster C (thrillers + transgressive literary, 11 books):** Lee Child,
  Palahniuk, Bukowski, Hemingway → *Snuff, The Hard Way, American Tabloid,
  Lawrence Block, Stephen King's Billy Summers…* — **entirely absent from the
  baseline's top recommendations despite coming from 40% of the user's likes.**

### k=4 — splits "thrillers+literary" into two, and isolates the Reacher run

Same SF and Doctorow clusters, but the third group splits into:

- **Transgressive/literary (6 books):** Palahniuk + Bukowski + Hemingway →
  *Pulp, Ham on Rye, Snuff, Diary, Stranger Than Fiction…*
- **Jack Reacher thrillers (all 5 Lee Child books)** → *The Sentinel, Worth
  Dying For, The Hard Way, A Wanted Man…*

This matches the exact flavour the user described ("some Jack Reacher books and
some sci-fi" as separate clusters).

### k=5 — splits classic-robots/Asimov off into its own cluster

A fifth cluster peels the three Asimov books out of the SF group

→ *Foundation and Empire, The Robots of Dawn, Robots and Empire, The Foundation
Trilogy, The Complete Robot…* — a tightly coherent recommendations set that was
previously diluted inside the big SF centroid.

### Takeaway

Every increase in k revealed recommendations that are genuinely good matches
for some part of the user's taste but were completely invisible under the single
centroid. At all three values (3, 4, 5) the clustering is stable, the clusters
have high internal cohesion, and the per-cluster recommendations are
subjectively much better — each side of the taste actually gets represented
instead of being averaged away. Fantasy didn't form its own cluster in this
dataset because there was only one fantasy title in the likes (Islington); a
cluster needs a couple of liked books to cohere, which is the right behaviour.
With a few more fantasy likes, a fantasy cluster would split off just as cleanly
as the Reacher cluster did at k=4.

## How it could work (concepts only)

The technique is independent of language, database, or library choice.

1. **Gather liked vectors.** Take every book the user rated positively, pull its
   embedding vector. At this scale (dozens of vectors per user) the whole set
   fits trivially in memory.

2. **Cluster them.** Run a partitional clustering algorithm (k-means, or any
   equivalent) over the liked vectors. Because the embeddings are L2-normalised,
   clustering in vector space with Euclidean distance is equivalent to spherical
   clustering with cosine distance — so the natural "books are similar when
   their embeddings point the same direction" intuition falls out for free. Each
   cluster gets a centroid (the mean of its members, re-normalised) and a label
   derived from its nearest member titles (e.g. the representative liked books).

3. **Choose k.** Start with a user-tunable knob (e.g. `k=3`). Letting the
   algorithm pick k automatically later — via an internal-validation score like
   silhouette, or a simple "don't make clusters of size 1" floor — is a natural
   next step but not required to ship.

4. **Recommend per cluster.** For each centroid, rank the full corpus by
   cosine similarity and take the top-N, excluding the books the user has
   already rated (and optionally excluding already-rated authors, the existing
   "discover new authors" mode). This produces k independent ranked lists,
   each tagged with its cluster label.

5. **Handle signed ratings.** Likes and dislikes both exist. The clustered
   ranking can use both: cluster only on likes (dislikes aren't "taste
   clusters", they're anti-taste), then at the per-cluster ranking step push
   each candidate away from disliked books (e.g. subtract a disliked-book
   contribution from its score, or filter candidates too close to a disliked
   book). This generalises the existing signed-weighted-centroid idea to the
   multi-cluster case.

6. **Dedupe across clusters.** A candidate may rank highly for two centroids
   (a book sitting between two clusters). Keep it once, recording which cluster
   claimed it or its best score — but again, how the lists are *merged* for
   display is out of scope here.

The only arithmetic involved is: mean-of-vectors (for centroids and updates),
dot-products (for all similarity scoring, since vectors are normalised), and an
argmax assignment (for the cluster step). Everything else is bookkeeping. There
is no need for an approximate-nearest-neighbour index — brute force over ~28k
vectors is sub-second, and the clustering only ever touches the user's own
dozens of liked vectors.

## Friction with the current setup

- **No native k-means in the database engine we use.** All vector math so far
  (cosine similarity, the existing centroid) is done in SQL via the engine's
  `list_cosine_similarity` function, which works great for ranking candidates
  against a precomputed point. But the engine has no built-in iterative
  clustering routine, so the cluster step — the repeated assign-means-update
  loop — can't be expressed purely as a SQL query against the canned functions.
  It has to move out to general-purpose code (a handful of matrix operations)
  for just that one step. The per-cluster *ranking* can still come back into the
  database as plain nearest-neighbour queries against a few centroid vectors.

- **Tiny working set, but it lives next to a big locked store.** The liked
  vectors are few and are conceptually cheap to cluster, but they're joined
  against the on-demand-embedded likes (books outside the pre-embedded corpus)
  which live in the same single-writer database the server already holds a lock
  on. A separate process can't open that database read-only to read them, so
  the clustering has to happen either inside the existing server process (which
  already has the connection and sees every vector source, including
  on-demand ones), or be fed the on-demand vectors through some side channel.
  This isn't a deep problem — the natural place to cluster is right where the
  recommendation request is already served — but it does pin the work to the
  server rather than letting it be a free-standing script the way the embedding
  driver is.

- **Centroid ranking is one query; per-cluster ranking is k queries.** The
  existing recommender is a single ORDER BY over the corpus. A per-cluster
  version repeats that scan k times (or unions k windowed rankings), which is
  still sub-second at this scale but is a k-fold constant-factor cost worth
  noting. It can be mitigated by computing all k rankings in one pass (cross
  join the corpus against the k centroids, rank with a window function per
  centroid), but that's a more complex query than today's straight sort.

- **Choosing k is a UX/product question, not just a technical one.** Too few
  clusters reproduces the averaging problem; too many produces singleton
  clusters that just echo a single liked book back as its own nearest
  neighbours (e.g. the 2-book Doctorow cluster already borders on this — its
  top recs are almost all more Doctorow). A floor on cluster size, or an
  auto-k scheme, will be needed before this feels polished.