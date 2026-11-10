package probe

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"testing/iotest"
	"time"
)

func saveCollectionPoll(t *testing.T, s *Store, completed int64, success bool, kind string) {
	t.Helper()
	p := Poll{Source: "wallstreetcn", Started: completed - 15, Completed: completed, Success: success}
	if kind != "legacy" {
		p.Requests = []SourceWindow{{ErrorKind: kind}}
	}
	if err := s.SavePoll(p); err != nil {
		t.Fatal(err)
	}
}

func TestCollectionRetryPersistsAndResets(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(1700000000, 0)
	for i, wait := range []time.Duration{30 * time.Second, time.Minute, 2 * time.Minute, 4 * time.Minute, 5 * time.Minute, 5 * time.Minute} {
		saveCollectionPoll(t, s, now.Unix(), false, "timeout")
		if s.wallstreetDue(now.Add(wait-time.Second), 2*time.Minute) || !s.wallstreetDue(now.Add(wait), 2*time.Minute) {
			t.Fatalf("attempt %d did not respect %s backoff", i+1, wait)
		}
	}
	if err = s.DB.Close(); err != nil {
		t.Fatal(err)
	}
	s, err = OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer s.DB.Close()
	if s.wallstreetDue(now.Add(299*time.Second), 2*time.Minute) {
		t.Fatal("restart reset backoff")
	}
	saveCollectionPoll(t, s, now.Unix(), true, "")
	if s.wallstreetDue(now.Add(119*time.Second), 2*time.Minute) || !s.wallstreetDue(now.Add(120*time.Second), 2*time.Minute) {
		t.Fatal("successful collection changed normal cadence")
	}
	saveCollectionPoll(t, s, now.Unix(), false, "network")
	if !s.wallstreetDue(now.Add(30*time.Second), 2*time.Minute) {
		t.Fatal("success did not reset transient failure streak")
	}
}

func TestCollectionRetryClassifications(t *testing.T) {
	for _, tc := range []struct {
		kind string
		wait time.Duration
	}{
		{"network", 30 * time.Second}, {"timeout", 30 * time.Second}, {"server", 30 * time.Second}, {"body_read", 30 * time.Second},
		{"rate_limit", 5 * time.Minute}, {"http", 5 * time.Minute}, {"invalid_json", 5 * time.Minute}, {"empty", 5 * time.Minute}, {"legacy", 5 * time.Minute}, {"cancelled", 5 * time.Minute},
	} {
		t.Run(tc.kind, func(t *testing.T) {
			a := testApp(t)
			now := time.Unix(1700000000, 0)
			if !a.Store.wallstreetDue(now, 2*time.Minute) {
				t.Fatal("empty collector should run")
			}
			saveCollectionPoll(t, a.Store, now.Unix(), false, tc.kind)
			if a.Store.wallstreetDue(now.Add(tc.wait-time.Second), 2*time.Minute) || !a.Store.wallstreetDue(now.Add(tc.wait), 2*time.Minute) {
				t.Fatal("incorrect retry boundary")
			}
		})
	}
	a := testApp(t)
	now := time.Unix(1700000000, 0)
	p := Poll{Source: "wallstreetcn", Completed: now.Unix(), Requests: []SourceWindow{{ErrorKind: "server", RetryAfter: now.Add(time.Hour).Unix()}}}
	if err := a.Store.SavePoll(p); err != nil {
		t.Fatal(err)
	}
	if a.Store.wallstreetDue(now.Add(59*time.Minute), 2*time.Minute) || !a.Store.wallstreetDue(now.Add(time.Hour), 2*time.Minute) {
		t.Fatal("server Retry-After was ignored")
	}
	if err := a.Store.SavePoll(Poll{Source: "trendradar", Completed: time.Now().Add(-2 * time.Minute).Unix()}); err != nil {
		t.Fatal(err)
	}
	if a.due("trendradar", time.Minute) {
		t.Fatal("TrendRadar failure schedule changed")
	}
}

func TestSourceRetryAfter(t *testing.T) {
	now := time.Date(2026, 11, 10, 12, 0, 0, 0, time.UTC)
	for _, tc := range []struct {
		value string
		want  int64
	}{
		{"60", now.Unix() + 60}, {" 0 ", now.Unix()}, {now.Add(time.Hour).Format(http.TimeFormat), now.Add(time.Hour).Unix()},
		{"-1", 0}, {"9223372036854775807", 0}, {"invalid", 0}, {now.Add(-time.Hour).Format(http.TimeFormat), 0},
	} {
		if got := sourceRetryAfter(tc.value, now); got != tc.want {
			t.Errorf("Retry-After %q: %d, want %d", tc.value, got, tc.want)
		}
	}
}

func TestCollectionResponseEvidence(t *testing.T) {
	for _, tc := range []struct {
		name, body, kind string
		status           int
		transportError   error
		bodyError        error
	}{
		{name: "timeout", kind: "timeout", transportError: context.DeadlineExceeded},
		{name: "network", kind: "network", transportError: errors.New("connection reset")},
		{name: "partial_body", status: 200, body: `{"data":`, bodyError: io.ErrUnexpectedEOF, kind: "body_read"},
		{name: "body_timeout", status: 200, body: `{"data":`, bodyError: context.DeadlineExceeded, kind: "timeout"},
		{name: "rate_limit", status: 429, kind: "rate_limit"}, {name: "unavailable", status: 503, kind: "server"},
		{name: "forbidden", status: 403, kind: "http"}, {name: "bad_json", status: 200, body: "{", kind: "invalid_json"},
		{name: "empty", status: 200, body: `{"data":{"items":[]}}`, kind: "empty"},
		{name: "invalid", status: 200, body: `{"data":{"items":[{"id":0}]}}`, kind: "invalid_items"},
		{name: "success", status: 200, body: `{"data":{"items":[{"id":123,"content_text":"message","display_time":1700000000}]}}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			client := &http.Client{Transport: fakeTransport(func(r *http.Request) (*http.Response, error) {
				calls++
				if tc.transportError != nil {
					return nil, tc.transportError
				}
				var reader io.Reader = strings.NewReader(tc.body)
				if tc.bodyError != nil {
					reader = io.MultiReader(reader, iotest.ErrReader(tc.bodyError))
				}
				return &http.Response{StatusCode: tc.status, Header: http.Header{"Retry-After": []string{"600"}}, Body: io.NopCloser(reader)}, nil
			})}
			_, window, err := fetchWallstreet(context.Background(), client, "https://example.test/feed?cursor=123")
			if calls != 1 || (err != nil) != (tc.kind != "") || window.ErrorKind != tc.kind || window.HTTPStatus != tc.status || window.Cursor != 123 || window.StartedMS <= 0 || window.CompletedMS < window.StartedMS {
				t.Fatalf("calls=%d window=%+v err=%v", calls, window, err)
			}
			if tc.status == 200 {
				if window.BodySHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte(tc.body))) || window.BodyBytes != len(tc.body) || window.BodyComplete != (tc.bodyError == nil) {
					t.Fatalf("body evidence: %+v", window)
				}
			}
			if tc.status == 429 || tc.status == 503 {
				if window.RetryAfter < time.Now().Add(599*time.Second).Unix() {
					t.Fatal("Retry-After not captured")
				}
			}
			if tc.kind == "" && (window.ItemCount != 1 || len(window.Items) != 1 || window.Items[0].ID != 123 || window.Items[0].PublishedAt != 1700000000) {
				t.Fatalf("item evidence: %+v", window)
			}
		})
	}
}

func TestCollectionBoundsAndCancellation(t *testing.T) {
	for _, oversized := range []bool{false, true} {
		var body string
		if oversized {
			body = strings.Repeat("x", 4*1024*1024+2)
		} else {
			items := []string{}
			for i := 1; i <= 101; i++ {
				items = append(items, fmt.Sprintf(`{"id":%d,"title":"message"}`, i))
			}
			body = `{"data":{"items":[` + strings.Join(items, ",") + `]}}`
		}
		client := &http.Client{Transport: fakeTransport(func(*http.Request) (*http.Response, error) {
			return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body))}, nil
		})}
		items, window, err := fetchWallstreet(context.Background(), client, "https://example.test/feed")
		if oversized {
			if err == nil || window.ErrorKind != "oversized" || window.BodyComplete || window.BodyBytes != 4*1024*1024+1 || len(items) != 0 {
				t.Fatalf("oversized body: %+v %v", window, err)
			}
		} else if err != nil || len(items) != 101 || window.ItemCount != 101 || len(window.Items) != 100 || !window.ItemsTruncated {
			t.Fatalf("bounded metadata changed collection: %+v %v", window, err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	client := &http.Client{Transport: fakeTransport(func(r *http.Request) (*http.Response, error) { return nil, r.Context().Err() })}
	_, window, err := fetchWallstreet(ctx, client, "https://example.test/feed")
	if err == nil || window.ErrorKind != "cancelled" {
		t.Fatal(window, err)
	}
}

func TestCollectionPaginationFailureAndRecoveryEvidence(t *testing.T) {
	a := testApp(t)
	phase, calls := 0, 0
	a.Client = &http.Client{Transport: fakeTransport(func(r *http.Request) (*http.Response, error) {
		calls++
		id, published := 1, 100
		if phase > 0 && r.URL.Query().Get("cursor") == "" {
			id, published = 2, 200
		} else if phase == 1 {
			return nil, context.DeadlineExceeded
		}
		body := fmt.Sprintf(`{"data":{"items":[{"id":%d,"title":"message","display_time":%d}]}}`, id, published)
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body))}, nil
	})}
	a.collectProbeSource(context.Background(), "https://example.test/feed", "")
	phase = 1
	a.collectProbeSource(context.Background(), "https://example.test/feed", "")
	history, err := a.Store.collectionHistory(0)
	if err != nil || len(history) != 2 {
		t.Fatal(history, err)
	}
	p := history[0]
	if calls != 3 || !p.Success || p.Gap == "" || p.Added != 1 || len(p.Requests) != 2 || p.Requests[0].Items[0].ID != 2 || p.Requests[1].ErrorKind != "timeout" || p.Requests[1].Cursor != 200 {
		t.Fatalf("partial collection lost evidence: %+v calls=%d", p, calls)
	}
	if a.Store.wallstreetDue(time.Unix(p.Completed+29, 0), 2*time.Minute) || !a.Store.wallstreetDue(time.Unix(p.Completed+30, 0), 2*time.Minute) {
		t.Fatal("partial collection did not retry transient backfill failure")
	}
	// A later window with an earlier publication time is observable without asserting
	// why the upstream exposed it late. Existing ingestion also keeps revision evidence.
	phase = 2
	a.collectProbeSource(context.Background(), "https://example.test/feed", "")
	history, err = a.Store.collectionHistory(0)
	if err != nil || history[0].Gap != "" || history[1].Gap == "" || len(history[0].Requests) != 1 {
		t.Fatal("recovery overwrote the original failure", history, err)
	}
}

func TestCollectionHistoryAuthPaginationRetentionAndAtomicity(t *testing.T) {
	a := testApp(t)
	now := time.Now()
	// Legacy positional INSERT stays compatible; missing evidence is an empty list.
	if _, err := a.Store.DB.Exec(`INSERT INTO polls VALUES(NULL,'wallstreetcn',?,?,1,0,0,'','')`, now.Unix(), now.Unix()); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 21; i++ {
		saveCollectionPoll(t, a.Store, now.Unix(), true, "")
	}
	w := httptest.NewRecorder()
	a.Handler().ServeHTTP(w, httptest.NewRequest("GET", prefix+"news/collection", nil))
	if w.Code != 401 {
		t.Fatal("collection history requires a session", w.Code)
	}
	read := func(path string) (result struct {
		Polls []CollectionPoll `json:"polls"`
		Next  int64            `json:"next"`
	}) {
		t.Helper()
		w := httptest.NewRecorder()
		a.Handler().ServeHTTP(w, authed(t, a, "GET", path, ""))
		if w.Code != 200 || w.Header().Get("Cache-Control") != "no-store" {
			t.Fatal(w.Code, w.Body.String(), w.Header())
		}
		if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		return
	}
	first := read("news/collection")
	if len(first.Polls) != 20 || first.Next != first.Polls[19].ID || len(first.Polls[0].Requests) != 1 {
		t.Fatal(first)
	}
	second := read(fmt.Sprintf("news/collection?before=%d", first.Next))
	if len(second.Polls) != 2 || second.Next != 0 || second.Polls[0].ID >= first.Next || len(second.Polls[1].Requests) != 0 {
		t.Fatal(second)
	}
	for _, value := range []string{"-1", "0", "abc", "9223372036854775808"} {
		w := httptest.NewRecorder()
		a.Handler().ServeHTTP(w, authed(t, a, "GET", "news/collection?before="+value, ""))
		if w.Code != 400 {
			t.Fatal("invalid cursor accepted", value, w.Code)
		}
	}
	if _, err := a.Store.DB.Exec(`CREATE TEMP TRIGGER reject_evidence BEFORE INSERT ON poll_requests BEGIN SELECT RAISE(ABORT,'fixture'); END`); err != nil {
		t.Fatal(err)
	}
	if err := a.Store.SavePoll(Poll{Source: "wallstreetcn", Requests: []SourceWindow{{}}}); err == nil {
		t.Fatal("evidence write failure was hidden")
	}
	var count int
	if err := a.Store.DB.QueryRow(`SELECT COUNT(*) FROM polls`).Scan(&count); err != nil || count != 22 {
		t.Fatal("failed evidence left a committed poll", count, err)
	}
	if _, err := a.Store.DB.Exec(`DROP TRIGGER reject_evidence`); err != nil {
		t.Fatal(err)
	}
	saveCollectionPoll(t, a.Store, now.AddDate(0, 0, -8).Unix(), false, "timeout")
	saveCollectionPoll(t, a.Store, now.AddDate(0, 0, -31).Unix(), false, "timeout")
	if err := a.Store.Prune(now); err != nil {
		t.Fatal(err)
	}
	if err := a.Store.DB.QueryRow(`SELECT COUNT(*) FROM poll_requests`).Scan(&count); err != nil || count != 21 {
		t.Fatal("evidence retention/cascade", count, err)
	}
	if err := a.Store.DB.QueryRow(`SELECT COUNT(*) FROM polls`).Scan(&count); err != nil || count != 23 {
		t.Fatal("poll history retention changed", count, err)
	}
}

func TestLatencyUsesFirstObservedPublication(t *testing.T) {
	a := testApp(t)
	now := time.Now().Unix()
	item := Item{Source: "wallstreetcn", SourceID: "1", Title: "message", PublishedAt: now - 120}
	if _, _, err := a.Store.SaveItems([]Item{item}, now); err != nil {
		t.Fatal(err)
	}
	item.PublishedAt = now - 3600
	if _, _, err := a.Store.SaveItems([]Item{item}, now+60); err != nil {
		t.Fatal(err)
	}
	stats, err := a.Store.Latencies(now - 1)
	if err != nil || len(stats) != 1 || stats[0].P95 != 120 || stats[0].Within5 != 1 {
		t.Fatal("publication revision rewrote historical latency", stats, err)
	}
	if _, err := a.Store.DB.Exec(`DELETE FROM news_revision_evidence`); err != nil {
		t.Fatal(err)
	}
	stats, err = a.Store.Latencies(now - 1)
	if err != nil || len(stats) != 1 || stats[0].Unknown != 1 || stats[0].Samples != 0 {
		t.Fatal("missing legacy publication evidence was guessed", stats, err)
	}
}

func TestCollectionWindowsPreserveLateArrivalAndPublicationEdits(t *testing.T) {
	a := testApp(t)
	now := time.Now().Unix()
	responses := []string{
		fmt.Sprintf(`{"data":{"items":[{"id":1,"title":"known","display_time":%d}]}}`, now-60),
		fmt.Sprintf(`{"data":{"items":[{"id":2,"title":"late","display_time":%d},{"id":1,"title":"known","display_time":%d}]}}`, now-3600, now-60),
		fmt.Sprintf(`{"data":{"items":[{"id":2,"title":"late","display_time":%d},{"id":1,"title":"known","display_time":%d}]}}`, now-120, now-60),
	}
	for _, body := range responses {
		a.Client = &http.Client{Transport: fakeTransport(func(*http.Request) (*http.Response, error) {
			return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body))}, nil
		})}
		a.collectProbeSource(context.Background(), "https://example.test/feed", "")
	}
	history, err := a.Store.collectionHistory(0)
	if err != nil || len(history) != 3 {
		t.Fatal(history, err)
	}
	for _, p := range history {
		if !p.Success || p.Gap != "" || len(p.Requests) != 1 || p.Requests[0].ItemsTruncated || !p.Requests[0].BodyComplete {
			t.Fatalf("fixture window incomplete: %+v", p)
		}
	}
	if len(history[2].Requests[0].Items) != 1 || history[2].Requests[0].Items[0].ID != 1 || history[1].Requests[0].Items[0].ID != 2 || history[1].Requests[0].Items[0].PublishedAt != now-3600 || history[0].Requests[0].Items[0].PublishedAt != now-120 {
		t.Fatal("arrival/publication sequence overwritten", history)
	}
	var firstSeen, originalPublication int64
	if err := a.Store.DB.QueryRow(`SELECT n.first_seen,e.published_at FROM news n JOIN news_revision_evidence e ON e.news_id=n.id AND e.version=1 WHERE n.source_id='2'`).Scan(&firstSeen, &originalPublication); err != nil || originalPublication != now-3600 {
		t.Fatal(firstSeen, originalPublication, err)
	}
	stats, err := a.Store.Latencies(now - 1)
	if err != nil || len(stats) != 1 || stats[0].P95 != firstSeen-originalPublication {
		t.Fatal("late publication disappeared from statistics", stats, err)
	}
}
