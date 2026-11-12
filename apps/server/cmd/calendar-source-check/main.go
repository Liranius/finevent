// calendar-source-check diagnoses the October 2026 source acceptance failures.
// It only fetches public schedules; it never opens a database or application config.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"hash"
	"io"
	"net"
	"net/http"
	"net/http/httptrace"
	"os"
	"time"

	"finevent/internal/calendar"
)

type target struct{ ID, URL, Parser string }

// These are the current index and official linked pages checked during acceptance.
// The fixed annual pages make this a diagnostic of this incident, not a collector.
var targets = []target{
	{"nbs_index", "https://www.stats.gov.cn/xxgk/sjfb/fbrcb/", ""},
	{"nbs_2026", "https://www.stats.gov.cn/xxgk/sjfb/fbrcb/202512/t20251224_1962137.html", "nbs"},
	{"bls_ics", "https://www.bls.gov/schedule/news_release/bls.ics", "bls"},
	{"bls_2026_html", "https://www.bls.gov/schedule/2026/home.htm", ""},
}

func diagnosticTargets(candidate bool) ([]target, time.Duration, string) {
	if candidate {
		return []target{targets[0], targets[1],
			{"newyorkfed_2026_11", "https://www.newyorkfed.org/research/calendars/i-nov26.html", ""}}, calendar.RequestTimeout, "candidate"
	}
	return targets, 15 * time.Second, "deployed_baseline"
}

type result struct {
	ID               string `json:"id"`
	URL              string `json:"url"`
	Started          string `json:"started"`
	DurationMS       int64  `json:"duration_ms"`
	FirstByteMS      int64  `json:"first_byte_ms"`
	Status           int    `json:"http_status"`
	Protocol         string `json:"protocol"`
	ContentLength    int64  `json:"content_length"`
	Uncompressed     bool   `json:"auto_decompressed"`
	BodyBytes        int64  `json:"body_bytes"`
	BodySHA256       string `json:"body_sha256"`
	FetchError       string `json:"fetch_error,omitempty"`
	RequestErrorKind string `json:"request_error_kind,omitempty"`
	ReadErrorKind    string `json:"read_error_kind,omitempty"`
	ParseError       string `json:"parse_error,omitempty"`
	ParsedEvents     int    `json:"parsed_events,omitempty"`
}

func errorKind(err error) string {
	if err == nil || errors.Is(err, io.EOF) {
		return ""
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "deadline_exceeded"
	}
	if errors.Is(err, io.ErrUnexpectedEOF) {
		return "unexpected_eof"
	}
	var ne net.Error
	if errors.As(err, &ne) && ne.Timeout() {
		return "timeout"
	}
	// Only types are exported: proxy credentials or environment values cannot leak.
	return fmt.Sprintf("%T", err)
}

type observedBody struct {
	io.ReadCloser
	result *result
	hash   hash.Hash
}

func (b *observedBody) Read(p []byte) (int, error) {
	n, err := b.ReadCloser.Read(p)
	b.result.BodyBytes += int64(n)
	_, _ = b.hash.Write(p[:n])
	b.result.BodySHA256 = hex.EncodeToString(b.hash.Sum(nil))
	if kind := errorKind(err); kind != "" {
		b.result.ReadErrorKind = kind
	}
	return n, err
}

type observedTransport struct {
	base   http.RoundTripper
	result *result
}

func (t observedTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	r, err := t.base.RoundTrip(req)
	if err != nil {
		t.result.RequestErrorKind = errorKind(err)
		return nil, err
	}
	t.result.Status, t.result.Protocol = r.StatusCode, r.Proto
	t.result.ContentLength, t.result.Uncompressed = r.ContentLength, r.Uncompressed
	r.Body = &observedBody{r.Body, t.result, sha256.New()}
	return r, nil
}

func check(ctx context.Context, t target, transport http.RoundTripper, timeout time.Duration) result {
	start := time.Now()
	r := result{ID: t.ID, URL: t.URL, Started: start.UTC().Format(time.RFC3339), FirstByteMS: -1, ContentLength: -1}
	trace := &httptrace.ClientTrace{GotFirstResponseByte: func() { r.FirstByteMS = time.Since(start).Milliseconds() }}
	client := calendar.NewHTTPClient()
	client.Transport, client.Timeout = observedTransport{transport, &r}, timeout
	body, err := calendar.Fetch(httptrace.WithClientTrace(ctx, trace), client, t.URL)
	if err != nil {
		r.FetchError = err.Error()
	} else if t.Parser != "" {
		events, parseErr := calendar.Parse(t.Parser, t.URL, body, 2026)
		if parseErr != nil {
			r.ParseError = parseErr.Error()
		} else {
			r.ParsedEvents = len(events)
		}
	}
	r.DurationMS = time.Since(start).Milliseconds()
	return r
}

func main() {
	candidate := flag.Bool("candidate", false, "Check the longer calendar timeout and an official alternative without modifying production")
	flag.Parse()
	selected, timeout, mode := diagnosticTargets(*candidate)
	results := make([]result, 0, len(selected))
	for _, t := range selected {
		results = append(results, check(context.Background(), t, http.DefaultTransport, timeout))
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(map[string]any{"purpose": "read_only_source_diagnosis", "mode": mode, "request_timeout_seconds": timeout.Seconds(), "results": results}); err != nil {
		os.Exit(1)
	}
}
