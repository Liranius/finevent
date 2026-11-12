package probe

import (
	"context"
	"database/sql"
	"encoding/json"
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type probeTransport func(*http.Request) (*http.Response, error)

func (f probeTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func probeFixture() AIProbeRequest {
	return AIProbeRequest{ID: "test-probe-001", Key: "test-secret-key", Expires: time.Now().Add(10 * time.Minute).Unix(), Pricing: ProbePricing{Version: "2026-10-28-peak", Input: 2000, Cached: 40, Output: 8000}}
}

func TestAIProbeReservationSurvivesRestartAndExhaustion(t *testing.T) {
	a := testApp(t)
	r := probeFixture()
	if ok, err := a.reserveAIProbe(r); !ok || err != nil {
		t.Fatal(ok, err)
	}
	if ok, err := a.reserveAIProbe(r); ok || err != nil {
		t.Fatal("duplicate reservation", ok, err)
	}
	if _, err := a.Store.DB.Exec(`UPDATE ai_probe_runs SET reserved_nano=?`, monthlyBudgetNano); err != nil {
		t.Fatal(err)
	}
	r.ID = "another-probe-002"
	if ok, err := a.reserveAIProbe(r); ok || err != nil {
		t.Fatal("budget exceeded", ok, err)
	}
	zone, _ := time.LoadLocation("Asia/Shanghai")
	var month string
	if err := a.Store.DB.QueryRow(`SELECT month FROM ai_probe_runs LIMIT 1`).Scan(&month); err != nil || month != time.Now().In(zone).Format("2006-01") {
		t.Fatal(month, err)
	}
}

func TestAIProbeRejectsExpiredAndUnderfundedRequests(t *testing.T) {
	a := testApp(t)
	r := probeFixture()
	r.Expires = time.Now().Unix() - 1
	if ok, err := a.reserveAIProbe(r); ok || err == nil {
		t.Fatal("expired accepted")
	}
	r = probeFixture()
	r.Pricing.Output = 100000
	if ok, err := a.reserveAIProbe(r); ok || err == nil {
		t.Fatal("underfunded accepted")
	}
}

func TestAIProbeUsageAndValidation(t *testing.T) {
	valid := `{"test_only":true,"summary":"fictional fixture","conditions":["details pending"],"evidence":["fixture-1"]}`
	for _, tc := range []struct {
		name, content, finish, status string
		usage                         any
		code                          int
		known                         bool
	}{
		{"valid", valid, "stop", "succeeded", map[string]int{"prompt_tokens": 100, "prompt_cache_hit_tokens": 40, "completion_tokens": 50}, 200, true},
		{"bad_evidence", strings.ReplaceAll(valid, "fixture-1", "invented"), "stop", "invalid_output", map[string]int{"prompt_tokens": 100, "completion_tokens": 50}, 200, true},
		{"truncated", valid, "length", "invalid_output", map[string]int{"prompt_tokens": 100, "completion_tokens": 50}, 200, true},
		{"empty", "", "stop", "invalid_output", map[string]int{"prompt_tokens": 100, "completion_tokens": 50}, 200, true},
		{"missing_usage", valid, "stop", "usage_unknown", nil, 200, false},
		{"bad_cache", valid, "stop", "usage_unknown", map[string]int{"prompt_tokens": 100, "prompt_cache_hit_tokens": 101, "completion_tokens": 50}, 200, false},
		{"rate_limited", valid, "stop", "http_error", nil, 429, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a := testApp(t)
			request := probeFixture()
			if ok, err := a.reserveAIProbe(request); !ok || err != nil {
				t.Fatal(ok, err)
			}
			calls := 0
			client := &http.Client{Transport: probeTransport(func(r *http.Request) (*http.Response, error) {
				calls++
				if r.URL.String() != deepseekEndpoint || r.Header.Get("Authorization") != "Bearer test-secret-key" {
					t.Fatal("wrong endpoint or authorization")
				}
				var body map[string]any
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Fatal(err)
				}
				if body["model"] != "deepseek-flash" || body["max_tokens"] != float64(1024) || body["thinking"].(map[string]any)["type"] != "disabled" {
					t.Fatal(body)
				}
				data, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"finish_reason": tc.finish, "message": map[string]string{"content": tc.content}}}, "usage": tc.usage})
				return &http.Response{StatusCode: tc.code, Body: io.NopCloser(strings.NewReader(string(data))), Header: make(http.Header)}, nil
			})}
			a.executeAIProbe(context.Background(), request, client, func() {})
			var status string
			var cost sql.NullInt64
			var completed int64
			if err := a.Store.DB.QueryRow(`SELECT status,cost_upper_nano,completed FROM ai_probe_runs WHERE id=?`, request.ID).Scan(&status, &cost, &completed); err != nil {
				t.Fatal(err)
			}
			if status != tc.status || cost.Valid != tc.known || calls != 1 || completed == 0 {
				t.Fatal(status, cost, calls, completed)
			}
			if tc.name == "valid" && cost.Int64 != 521600 {
				t.Fatal("incorrect cache cost", cost)
			}
		})
	}
}

func TestAIProbeTimeoutKeepsReservation(t *testing.T) {
	a := testApp(t)
	r := probeFixture()
	if ok, err := a.reserveAIProbe(r); !ok || err != nil {
		t.Fatal(ok, err)
	}
	client := &http.Client{Transport: probeTransport(func(req *http.Request) (*http.Response, error) {
		<-req.Context().Done()
		return nil, req.Context().Err()
	})}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	a.executeAIProbe(ctx, r, client, func() {})
	var status string
	var cost sql.NullInt64
	if err := a.Store.DB.QueryRow(`SELECT status,cost_upper_nano FROM ai_probe_runs WHERE id=?`, r.ID).Scan(&status, &cost); err != nil {
		t.Fatal(err)
	}
	if status != "request_failed" || cost.Valid {
		t.Fatal(status, cost)
	}
}

func TestAIProbeRecordsPreciseModelAndCollectorIntervals(t *testing.T) {
	a := testApp(t)
	r := probeFixture()
	if ok, err := a.reserveAIProbe(r); !ok || err != nil {
		t.Fatal(ok, err)
	}
	started, collected, done := make(chan struct{}), make(chan struct{}), make(chan struct{})
	a.Client = &http.Client{Transport: probeTransport(func(_ *http.Request) (*http.Response, error) {
		close(collected)
		time.Sleep(10 * time.Millisecond)
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{"data":{"items":[{"id":1,"title":"fixture","display_time":100}]}}`)), Header: make(http.Header)}, nil
	})}
	client := &http.Client{Transport: probeTransport(func(req *http.Request) (*http.Response, error) {
		select {
		case <-collected:
		case <-req.Context().Done():
			return nil, req.Context().Err()
		}
		time.Sleep(20 * time.Millisecond)
		return &http.Response{StatusCode: 429, Body: io.NopCloser(strings.NewReader("")), Header: make(http.Header)}, nil
	})}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	go func() {
		defer close(done)
		a.executeAIProbe(ctx, r, client, func() { close(started) })
	}()
	<-started
	a.collectProbeSource(ctx, WallstreetURL, r.ID)
	<-done
	var modelStart, modelEnd, collectStart, collectEnd int64
	var success bool
	if err := a.Store.DB.QueryRow(`SELECT model_started_ms,model_completed_ms,collector_started_ms,collector_completed_ms,collector_success FROM ai_probe_timings WHERE id=?`, r.ID).Scan(&modelStart, &modelEnd, &collectStart, &collectEnd, &success); err != nil {
		t.Fatal(err)
	}
	if !success || modelStart <= 0 || modelStart > collectStart || collectEnd <= collectStart || collectEnd > modelEnd {
		t.Fatal("missing or disjoint timing evidence", modelStart, modelEnd, collectStart, collectEnd, success)
	}
	if ok, err := a.reserveAIProbe(r); ok || err != nil {
		t.Fatal("timing must not enable a second request", ok, err)
	}
}

func TestAIProbeTimingTableIsAdditiveAndPreservesLedger(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.DB.Exec(`INSERT INTO ai_probe_runs(id,month,started,status,reserved_nano,pricing) VALUES('old-probe','2026-10',100,'reserved',20000000,'{}'); DROP TABLE ai_probe_timings;`)
	s.DB.Close()
	if err != nil {
		t.Fatal(err)
	}
	s, err = OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer s.DB.Close()
	var charge int64
	if err = s.DB.QueryRow(`SELECT reserved_nano FROM ai_probe_runs WHERE id='old-probe'`).Scan(&charge); err != nil || charge != probeReserveNano {
		t.Fatal("old reservation lost", charge, err)
	}
	if _, err = s.DB.Exec(`INSERT INTO ai_probe_timings(id) VALUES('old-probe')`); err != nil {
		t.Fatal(err)
	}
}

func TestProbeWakeupDoesNotAddCollectionWithoutRequest(t *testing.T) {
	a := testApp(t)
	a.ProbeRequestPath = filepath.Join(t.TempDir(), "absent-request.json")
	if err := a.Store.SavePoll(Poll{Source: "wallstreetcn", Started: time.Now().Unix(), Completed: time.Now().Unix(), Success: true}); err != nil {
		t.Fatal(err)
	}
	calls := 0
	a.Client = &http.Client{Transport: probeTransport(func(r *http.Request) (*http.Response, error) {
		if r.URL.String() == WallstreetURL {
			calls++
		}
		return &http.Response{StatusCode: 503, Body: io.NopCloser(strings.NewReader("")), Header: make(http.Header)}, nil
	})}
	// Keep both collectors offline while testing the normal news cadence.
	a.CalendarClient = a.Client
	ctx, cancel := context.WithTimeout(context.Background(), 1200*time.Millisecond)
	defer cancel()
	a.Run(ctx, true, "")
	if calls != 0 {
		t.Fatal("a wakeup without a reserved probe must not bypass normal collection cadence", calls)
	}
}
