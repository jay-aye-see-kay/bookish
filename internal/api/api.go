// Package api wires the HTTP JSON endpoints over db + embed.
package api

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strconv"

	"github.com/jack/bookish/internal/db"
	"github.com/jack/bookish/internal/embed"
)

// Server holds dependencies for the HTTP handlers.
type Server struct {
	DB    *db.DB
	Embed *embed.Client
}

// Handler builds the routed http.Handler.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/health", s.health)
	mux.HandleFunc("GET /api/books", s.searchBooks)
	mux.HandleFunc("GET /api/preferences", s.withUser(s.listPreferences))
	mux.HandleFunc("PUT /api/preferences/{work_key...}", s.withUser(s.putPreference))
	mux.HandleFunc("DELETE /api/preferences/{work_key...}", s.withUser(s.deletePreference))
	mux.HandleFunc("GET /api/recommendations", s.withUser(s.recommendations))
	return mux
}

// --- helpers ---------------------------------------------------------------

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if v != nil {
		_ = json.NewEncoder(w).Encode(v)
	}
}

func writeErr(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

type userHandler func(w http.ResponseWriter, r *http.Request, username string)

// withUser enforces the trusted X-User header and auto-creates the user.
func (s *Server) withUser(h userHandler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		username := r.Header.Get("X-User")
		if username == "" {
			writeErr(w, http.StatusUnauthorized, "missing X-User header")
			return
		}
		if err := s.DB.EnsureUser(r.Context(), username); err != nil {
			log.Printf("ensure user %q: %v", username, err)
			writeErr(w, http.StatusInternalServerError, "internal error")
			return
		}
		h(w, r, username)
	}
}

// --- handlers --------------------------------------------------------------

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	cat, cor, err := s.DB.Counts(r.Context())
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "db error")
		return
	}
	embedStatus := "down"
	if s.Embed.Healthy(r.Context()) {
		embedStatus = "ok"
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":       "ok",
		"catalog":      cat,
		"corpus":       cor,
		"embed_server": embedStatus,
	})
}

func (s *Server) searchBooks(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query().Get("q")
	if q == "" {
		writeJSON(w, http.StatusOK, []db.Book{})
		return
	}
	limit := parseInt(r.URL.Query().Get("limit"), 20, 1, 100)
	books, err := s.DB.SearchBooks(r.Context(), q, limit)
	if err != nil {
		log.Printf("search %q: %v", q, err)
		writeErr(w, http.StatusInternalServerError, "search failed")
		return
	}
	if books == nil {
		books = []db.Book{}
	}
	writeJSON(w, http.StatusOK, books)
}

func (s *Server) listPreferences(w http.ResponseWriter, r *http.Request, username string) {
	ratings, err := s.DB.ListRatings(r.Context(), username)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "db error")
		return
	}
	if ratings == nil {
		ratings = []db.Rating{}
	}
	writeJSON(w, http.StatusOK, ratings)
}

func (s *Server) putPreference(w http.ResponseWriter, r *http.Request, username string) {
	workKey := "/" + r.PathValue("work_key")
	var body struct {
		Rating int `json:"rating"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	switch body.Rating {
	case -2, -1, 1, 2:
	default:
		writeErr(w, http.StatusBadRequest, "rating must be one of -2,-1,1,2")
		return
	}

	entry, err := s.DB.LookupWork(r.Context(), workKey)
	if errors.Is(err, sql.ErrNoRows) {
		writeErr(w, http.StatusNotFound, "unknown work_key")
		return
	}
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "db error")
		return
	}

	if err := s.DB.UpsertRating(r.Context(), username, workKey, entry.InputText, body.Rating); err != nil {
		writeErr(w, http.StatusInternalServerError, "db error")
		return
	}

	// Best-effort ensure-embedding — rating is already saved regardless.
	if err := s.ensureVec(r.Context(), entry.InputText); err != nil {
		log.Printf("ensure vec for %q: %v", entry.InputText, err)
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"work_key": workKey,
		"rating":   body.Rating,
	})
}

func (s *Server) deletePreference(w http.ResponseWriter, r *http.Request, username string) {
	workKey := "/" + r.PathValue("work_key")
	if _, err := s.DB.DeleteRating(r.Context(), username, workKey); err != nil {
		writeErr(w, http.StatusInternalServerError, "db error")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) recommendations(w http.ResponseWriter, r *http.Request, username string) {
	n := parseInt(r.URL.Query().Get("n"), 20, 1, 100)
	excludeAuthors := r.URL.Query().Get("exclude_rated_authors") == "true"

	// Ensure every rated book has a vector before ranking (out-of-corpus likes
	// need the embed server). If a needed vec can't be computed -> 502.
	if err := s.ensureRatedVecs(r.Context(), username); err != nil {
		writeErr(w, http.StatusBadGateway, "could not compute embedding: "+err.Error())
		return
	}

	recs, err := s.DB.Recommend(r.Context(), username, n, excludeAuthors)
	switch {
	case errors.Is(err, db.ErrNoRatings):
		writeErr(w, http.StatusBadRequest, "no ratings yet")
		return
	case errors.Is(err, db.ErrDegenerate):
		writeErr(w, http.StatusConflict, "preference vector is degenerate")
		return
	case err != nil:
		log.Printf("recommend: %v", err)
		writeErr(w, http.StatusInternalServerError, "recommend failed")
		return
	}
	writeJSON(w, http.StatusOK, recs)
}

// --- ensure-embedding ------------------------------------------------------

// ensureVec makes sure input_text has a vector: corpus/ondemand hit -> done;
// else embed via llama-server, L2-normalize, and insert into ondemand_vecs.
func (s *Server) ensureVec(ctx context.Context, inputText string) error {
	has, err := s.DB.HasVec(ctx, inputText)
	if err != nil {
		return err
	}
	if has {
		return nil
	}
	vec, err := s.Embed.Embed(ctx, inputText)
	if err != nil {
		return err
	}
	return s.DB.InsertOndemandVec(ctx, embed.Sha256Hex(inputText), inputText, vec)
}

// ensureRatedVecs ensures all of a user's rated books have vectors.
func (s *Server) ensureRatedVecs(ctx context.Context, username string) error {
	texts, err := s.DB.RatedInputTextsMissingVec(ctx, username)
	if err != nil {
		return err
	}
	for _, t := range texts {
		if err := s.ensureVec(ctx, t); err != nil {
			return err
		}
	}
	return nil
}

func parseInt(s string, def, min, max int) int {
	if s == "" {
		return def
	}
	n, err := strconv.Atoi(s)
	if err != nil {
		return def
	}
	if n < min {
		return min
	}
	if n > max {
		return max
	}
	return n
}
