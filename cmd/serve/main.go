// Command serve runs the bookish JSON backend: embedded DuckDB + HTTP API.
// The llama-server embedding backend is started separately (outside the
// sandbox) via bin/serve-embed.sh.
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/jack/bookish/internal/api"
	"github.com/jack/bookish/internal/db"
	"github.com/jack/bookish/internal/embed"
)

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func main() {
	var (
		addr      = getenv("BOOKISH_ADDR", ":8090")
		booksPath = getenv("BOOKISH_BOOKS_DB", "data/books.duckdb")
		appPath   = getenv("BOOKISH_APP_DB", "data/app.duckdb")
		servePath = getenv("BOOKISH_SERVE_SQL", "sql/serve.sql")
		embedURL  = getenv("BOOKISH_EMBED_URL", "http://localhost:8080")
	)

	serveSQL, err := db.ReadServeSQL(servePath)
	if err != nil {
		log.Fatalf("read %s: %v", servePath, err)
	}

	log.Printf("opening DuckDB (books=%s app=%s) and materializing corpus/catalog…", booksPath, appPath)
	start := time.Now()
	database, err := db.Open(booksPath, appPath, serveSQL)
	if err != nil {
		log.Fatalf("open db: %v", err)
	}
	defer database.Close()

	cat, cor, err := database.Counts(context.Background())
	if err != nil {
		log.Fatalf("counts: %v", err)
	}
	log.Printf("ready in %s — catalog=%d corpus=%d", time.Since(start).Round(time.Millisecond), cat, cor)

	embedClient := embed.New(embedURL)
	if embedClient.Healthy(context.Background()) {
		log.Printf("embed server %s: ok", embedURL)
	} else {
		log.Printf("embed server %s: DOWN (recommendations work offline for in-corpus likes)", embedURL)
	}

	srv := &api.Server{DB: database, Embed: embedClient}
	httpSrv := &http.Server{Addr: addr, Handler: srv.Handler()}
	log.Printf("listening on %s", addr)
	if err := httpSrv.ListenAndServe(); err != nil {
		log.Fatal(err)
	}
}
