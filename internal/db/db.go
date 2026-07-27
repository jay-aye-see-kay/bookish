// Package db owns the single embedded-DuckDB connection: attach, migrate,
// materialize working sets, and all query helpers. DuckDB is single-writer, so
// all writes are serialized behind one mutex.
package db

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"
	"os"
	"sync"
	"time"

	_ "github.com/marcboeker/go-duckdb/v2"
)

// MODEL_ID must match bin/embed.py verbatim — it's part of the cache identity.
const ModelID = "Qwen3-Embedding-8B-Q8_0/last/norm2/v2-book-pipe"

//go:embed migrations.sql
var migrationsSQL string

// DB wraps the embedded DuckDB connection.
type DB struct {
	sql *sql.DB
	mu  sync.Mutex // serializes writes (DuckDB single-writer)
}

// Open attaches books.duckdb (read-only) + app.duckdb (writable), runs
// migrations, then materializes corpus/catalog from serveSQL.
func Open(booksPath, appPath, serveSQL string) (*DB, error) {
	sqldb, err := sql.Open("duckdb", "")
	if err != nil {
		return nil, err
	}
	// One physical connection keeps ATTACH state + write serialization simple.
	sqldb.SetMaxOpenConns(1)

	ctx := context.Background()
	for _, stmt := range []string{
		fmt.Sprintf("ATTACH '%s' AS books (READ_ONLY)", booksPath),
		fmt.Sprintf("ATTACH '%s' AS app", appPath),
	} {
		if _, err := sqldb.ExecContext(ctx, stmt); err != nil {
			sqldb.Close()
			return nil, fmt.Errorf("%s: %w", stmt, err)
		}
	}

	// Migrations create app tables (needed before serve.sql's all_vecs view).
	if _, err := sqldb.ExecContext(ctx, migrationsSQL); err != nil {
		sqldb.Close()
		return nil, fmt.Errorf("migrations: %w", err)
	}
	if _, err := sqldb.ExecContext(ctx, serveSQL); err != nil {
		sqldb.Close()
		return nil, fmt.Errorf("serve.sql: %w", err)
	}

	return &DB{sql: sqldb}, nil
}

// ReadServeSQL loads the startup SQL from disk.
func ReadServeSQL(path string) (string, error) {
	b, err := os.ReadFile(path)
	return string(b), err
}

func (d *DB) Close() error { return d.sql.Close() }

// Counts reports catalog + corpus sizes for /api/health.
func (d *DB) Counts(ctx context.Context) (catalog, corpus int, err error) {
	if err = d.sql.QueryRowContext(ctx, "SELECT count(*) FROM catalog").Scan(&catalog); err != nil {
		return
	}
	err = d.sql.QueryRowContext(ctx, "SELECT count(*) FROM corpus").Scan(&corpus)
	return
}

// --- search ----------------------------------------------------------------

// Book is a catalog search result.
type Book struct {
	WorkKey      string `json:"work_key"`
	Author       string `json:"author"`
	Title        string `json:"title"`
	Year         int    `json:"year"`
	Editions     int    `json:"editions"`
	HasEmbedding bool   `json:"has_embedding"`
}

// SearchBooks does an ILIKE search over the materialized catalog.
func (d *DB) SearchBooks(ctx context.Context, q string, limit int) ([]Book, error) {
	const query = `
SELECT c.work_key, c.author, c.title, c.year, c.editions,
       EXISTS (SELECT 1 FROM corpus co WHERE co.work_key = c.work_key) AS has_embedding
FROM catalog c
WHERE c.title_lc LIKE '%' || lower(?) || '%'
   OR c.author_lc LIKE '%' || lower(?) || '%'
ORDER BY c.editions DESC
LIMIT ?`
	rows, err := d.sql.QueryContext(ctx, query, q, q, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Book
	for rows.Next() {
		var b Book
		if err := rows.Scan(&b.WorkKey, &b.Author, &b.Title, &b.Year, &b.Editions, &b.HasEmbedding); err != nil {
			return nil, err
		}
		out = append(out, b)
	}
	return out, rows.Err()
}

// CatalogEntry is a single work looked up by key.
type CatalogEntry struct {
	WorkKey   string
	Author    string
	Title     string
	Year      int
	Editions  int
	InputText string
}

// LookupWork returns the catalog entry for a work_key, or sql.ErrNoRows.
func (d *DB) LookupWork(ctx context.Context, workKey string) (CatalogEntry, error) {
	var e CatalogEntry
	err := d.sql.QueryRowContext(ctx,
		`SELECT work_key, author, title, year, editions, input_text
		 FROM catalog WHERE work_key = ?`, workKey).
		Scan(&e.WorkKey, &e.Author, &e.Title, &e.Year, &e.Editions, &e.InputText)
	return e, err
}

// --- ratings ---------------------------------------------------------------

// EnsureUser inserts the user if absent (auto-create on first use).
func (d *DB) EnsureUser(ctx context.Context, username string) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	_, err := d.sql.ExecContext(ctx,
		`INSERT INTO app.users (username, created_at)
		 VALUES (?, ?) ON CONFLICT DO NOTHING`, username, time.Now().UTC())
	return err
}

// Rating is a stored preference joined to catalog metadata.
type Rating struct {
	WorkKey string `json:"work_key"`
	Author  string `json:"author"`
	Title   string `json:"title"`
	Year    int    `json:"year"`
	Rating  int    `json:"rating"`
}

// ListRatings returns a user's ratings with book metadata.
func (d *DB) ListRatings(ctx context.Context, username string) ([]Rating, error) {
	const query = `
SELECT r.work_key, c.author, c.title, c.year, r.rating
FROM app.ratings r
LEFT JOIN catalog c USING (work_key)
WHERE r.username = ?
ORDER BY r.updated_at DESC`
	rows, err := d.sql.QueryContext(ctx, query, username)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Rating
	for rows.Next() {
		var r Rating
		var author, title sql.NullString
		var year sql.NullInt64
		if err := rows.Scan(&r.WorkKey, &author, &title, &year, &r.Rating); err != nil {
			return nil, err
		}
		r.Author, r.Title, r.Year = author.String, title.String, int(year.Int64)
		out = append(out, r)
	}
	return out, rows.Err()
}

// UpsertRating stores/updates a rating (with denormalized input_text).
func (d *DB) UpsertRating(ctx context.Context, username, workKey, inputText string, rating int) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	_, err := d.sql.ExecContext(ctx, `
INSERT INTO app.ratings (username, work_key, input_text, rating, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (username, work_key) DO UPDATE
  SET rating = excluded.rating,
      input_text = excluded.input_text,
      updated_at = excluded.updated_at`,
		username, workKey, inputText, rating, time.Now().UTC())
	return err
}

// DeleteRating removes a rating. Returns whether a row was deleted.
func (d *DB) DeleteRating(ctx context.Context, username, workKey string) (bool, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	res, err := d.sql.ExecContext(ctx,
		`DELETE FROM app.ratings WHERE username = ? AND work_key = ?`, username, workKey)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

// --- on-demand vectors -----------------------------------------------------

// HasVec reports whether input_text has a usable vector (corpus or on-demand).
func (d *DB) HasVec(ctx context.Context, inputText string) (bool, error) {
	var n int
	err := d.sql.QueryRowContext(ctx,
		`SELECT count(*) FROM all_vecs WHERE input_text = ?`, inputText).Scan(&n)
	return n > 0, err
}

// RatedInputTextsMissingVec returns distinct input_texts a user rated that
// have no vector yet (neither in corpus nor ondemand_vecs).
func (d *DB) RatedInputTextsMissingVec(ctx context.Context, username string) ([]string, error) {
	const query = `
SELECT DISTINCT r.input_text
FROM app.ratings r
WHERE r.username = ?
  AND r.input_text NOT IN (SELECT input_text FROM all_vecs)`
	rows, err := d.sql.QueryContext(ctx, query, username)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var t string
		if err := rows.Scan(&t); err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	return out, rows.Err()
}

// InsertOndemandVec stores an on-demand (out-of-corpus) embedding.
func (d *DB) InsertOndemandVec(ctx context.Context, sha, inputText string, vec []float32) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	_, err := d.sql.ExecContext(ctx, `
INSERT INTO app.ondemand_vecs (input_sha256, model_id, input_text, vec, created_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (input_sha256, model_id) DO NOTHING`,
		sha, ModelID, inputText, vec, time.Now().UTC())
	return err
}
