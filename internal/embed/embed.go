// Package embed talks to the local llama-server /v1/embeddings endpoint and
// mirrors bin/embed.py: input_text = "Book|author|title", vectors are
// L2-normalized client-side, cache key = sha256(utf8(input_text)) hex.
package embed

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"time"
)

const dim = 4096

// Client is a llama-server embeddings client.
type Client struct {
	baseURL string
	http    *http.Client
}

// New returns a client for a llama-server base URL (e.g. http://localhost:8080).
func New(baseURL string) *Client {
	return &Client{
		baseURL: baseURL,
		http:    &http.Client{Timeout: 300 * time.Second},
	}
}

// Healthy reports whether the embed server /health returns {"status":"ok"}.
func (c *Client) Healthy(ctx context.Context) bool {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/health", nil)
	if err != nil {
		return false
	}
	resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return false
	}
	var body struct {
		Status string `json:"status"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return false
	}
	return body.Status == "ok"
}

// Sha256Hex returns the cache key for an input string.
func Sha256Hex(inputText string) string {
	sum := sha256.Sum256([]byte(inputText))
	return hex.EncodeToString(sum[:])
}

type embedReq struct {
	Input []string `json:"input"`
	Model string   `json:"model"`
}

type embedResp struct {
	Data []struct {
		Index     int       `json:"index"`
		Embedding []float32 `json:"embedding"`
	} `json:"data"`
}

// Embed returns the L2-normalized embedding for a single input string.
func (c *Client) Embed(ctx context.Context, inputText string) ([]float32, error) {
	payload, err := json.Marshal(embedReq{Input: []string{inputText}, Model: "q"})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		c.baseURL+"/v1/embeddings", bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("embed server status %d", resp.StatusCode)
	}
	var body embedResp
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return nil, err
	}
	if len(body.Data) == 0 {
		return nil, fmt.Errorf("embed server returned no data")
	}
	// keep server order via index (single input, but stay faithful to embed.py)
	sort.Slice(body.Data, func(i, j int) bool { return body.Data[i].Index < body.Data[j].Index })
	vec := body.Data[0].Embedding
	if len(vec) != dim {
		return nil, fmt.Errorf("embed dim %d, want %d", len(vec), dim)
	}
	return normalize(vec), nil
}

// normalize L2-normalizes in place (server returns UN-normalized vectors).
func normalize(v []float32) []float32 {
	var sum float64
	for _, x := range v {
		sum += float64(x) * float64(x)
	}
	n := math.Sqrt(sum)
	if n == 0 {
		return v
	}
	for i, x := range v {
		v[i] = float32(float64(x) / n)
	}
	return v
}
