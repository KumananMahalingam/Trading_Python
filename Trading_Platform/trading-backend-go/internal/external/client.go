// Package external wraps the outbound HTTP calls to the market data providers,
// replacing the RestTemplate bean.
package external

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Client is a small JSON-over-HTTP helper with a sane timeout.
type Client struct {
	http *http.Client
}

// NewClient builds the shared client.
func NewClient() *Client {
	return &Client{
		http: &http.Client{Timeout: 15 * time.Second},
	}
}

// maxResponseBytes caps how much of a provider response we will read, so a
// misbehaving upstream cannot exhaust memory.
const maxResponseBytes = 8 << 20 // 8 MiB

// GetJSON fetches url and decodes the JSON body into out.
func (c *Client) GetJSON(ctx context.Context, url string, out any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Accept", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("perform request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("unexpected status %d from %s", resp.StatusCode, req.URL.Host)
	}

	body, err := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes))
	if err != nil {
		return fmt.Errorf("read response: %w", err)
	}

	if err := json.Unmarshal(body, out); err != nil {
		return fmt.Errorf("decode response: %w", err)
	}

	return nil
}
