package db

import (
	"context"
	"errors"
	"math"
)

// ErrNoRatings means the user has no ratings to build a preference vector from.
var ErrNoRatings = errors.New("no ratings")

// ErrDegenerate means the weighted centroid is ~zero (likes/dislikes cancel).
var ErrDegenerate = errors.New("degenerate preference vector")

// Recommendation is a scored book.
type Recommendation struct {
	WorkKey  string  `json:"work_key"`
	Author   string  `json:"author"`
	Title    string  `json:"title"`
	Year     int     `json:"year"`
	Editions int     `json:"editions"`
	Score    float64 `json:"score"`
}

// Recommend ranks corpus books by cosine to the user's signed-weighted centroid.
// excludeRatedAuthors drops books by any author the user has already rated.
func (d *DB) Recommend(ctx context.Context, username string, n int, excludeRatedAuthors bool) ([]Recommendation, error) {
	// Guard: need at least one rated book that has a vector.
	var nLiked int
	if err := d.sql.QueryRowContext(ctx, `
SELECT count(*) FROM app.ratings r JOIN all_vecs v USING (input_text)
WHERE r.username = ?`, username).Scan(&nLiked); err != nil {
		return nil, err
	}
	if nLiked == 0 {
		return nil, ErrNoRatings
	}

	const query = `
WITH liked AS (
  SELECT v.vec, r.rating::DOUBLE AS weight
  FROM app.ratings r JOIN all_vecs v USING (input_text)
  WHERE r.username = ?),
exploded AS (
  SELECT u.i AS idx, u.val * l.weight AS wv
  FROM liked l, unnest(l.vec) WITH ORDINALITY AS u(val, i)),
centroid AS (
  SELECT list(s ORDER BY idx)::FLOAT[] AS p
  FROM (SELECT idx, sum(wv) AS s FROM exploded GROUP BY idx))
SELECT c.work_key, c.author, c.title, c.year, c.editions,
       list_cosine_similarity(c.vec, centroid.p) AS score
FROM corpus c, centroid
WHERE c.work_key NOT IN (SELECT work_key FROM app.ratings WHERE username = ?)
  AND (? = false
       OR c.author NOT IN (SELECT cat.author FROM catalog cat
                           JOIN app.ratings r USING (work_key) WHERE r.username = ?))
ORDER BY score DESC
LIMIT ?`
	rows, err := d.sql.QueryContext(ctx, query,
		username, username, excludeRatedAuthors, username, n)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Recommendation
	for rows.Next() {
		var r Recommendation
		if err := rows.Scan(&r.WorkKey, &r.Author, &r.Title, &r.Year, &r.Editions, &r.Score); err != nil {
			return nil, err
		}
		if math.IsNaN(r.Score) {
			return nil, ErrDegenerate
		}
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if len(out) == 0 {
		return nil, ErrDegenerate
	}
	return out, nil
}
