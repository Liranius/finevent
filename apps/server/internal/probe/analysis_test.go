package probe

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func analysisFixture(t *testing.T) (*App, int64) {
	t.Helper()
	a := testApp(t)
	a.AIConfig = DefaultAIConfig()
	a.AIConfig.Key = "unit-test-secret-only"
	a.AIConfig.ValidUntil = time.Now().AddDate(1, 0, 0).Format("2006-01-02")
	now := time.Now().Unix()
	_, _, err := a.Store.SaveItems([]Item{{Source: "wallstreetcn", SourceID: "analysis-test", Title: "央行宣布降准", Content: "测试资料：央行宣布降准0.5个百分点，实施细则待公布。", URL: "https://example.org/analysis", PublishedAt: now - 60}}, now)
	if err != nil {
		t.Fatal(err)
	}
	var id int64
	if err = a.Store.DB.QueryRow(`SELECT id FROM news LIMIT 1`).Scan(&id); err != nil {
		t.Fatal(err)
	}
	if err = a.Store.SaveAISettings(context.Background(), AISettings{Enabled: true}, now); err != nil {
		t.Fatal(err)
	}
	return a, id
}

const goodAnalysis = `{"facts":[{"text":"测试来源宣布降准","evidence":"e1","quote":"央行宣布降准0.5个百分点"}],"categories":["monetary"],"impacts":[{"target":"银行","market":"A股","industry":"金融","direction":"mixed","path":"流动性增加，盈利影响仍需核对","conditions":["实施细则落地"],"horizon":"短期至中期","counterfactors":["需求偏弱"],"evidence":["e1"]}],"metrics":[{"name":"降准幅度","actual":{"value":"0.5个百分点","evidence":"e1","quote":"央行宣布降准0.5个百分点"},"expected":null,"previous":null}],"uncertainty":"实施细则待公布"}`

func analysisClient(content, finish string, usage any, code int) *http.Client {
	return &http.Client{Transport: probeTransport(func(r *http.Request) (*http.Response, error) {
		b, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"finish_reason": finish, "message": map[string]string{"content": content}}}, "usage": usage})
		return &http.Response{StatusCode: code, Body: io.NopCloser(strings.NewReader(string(b))), Header: make(http.Header)}, nil
	})}
}
func queueFixture(t *testing.T, a *App, id int64) string {
	t.Helper()
	key, err := a.Store.QueueAnalysis(context.Background(), id, a.AIConfig, false, time.Now().Unix())
	if err != nil {
		t.Fatal(err)
	}
	return key
}
func TestAnalysisValidationAccountingAndFailures(t *testing.T) {
	known := map[string]int64{"prompt_tokens": 100, "prompt_cache_hit_tokens": 20, "completion_tokens": 200}
	for _, tc := range []struct {
		name, content, finish, want string
		usage                       any
		code                        int
		charged                     bool
	}{
		{"valid", goodAnalysis, "stop", "succeeded", known, 200, true},
		{"invented_reference", strings.ReplaceAll(goodAnalysis, "e1", "e9"), "stop", "invalid_output", known, 200, true},
		{"invented_quote", strings.ReplaceAll(goodAnalysis, "央行宣布降准0.5个百分点", "不存在的引文"), "stop", "invalid_output", known, 200, true},
		{"invented_number", strings.Replace(goodAnalysis, `"value":"0.5个百分点"`, `"value":"9个百分点"`, 1), "stop", "invalid_output", known, 200, true},
		{"empty", "", "stop", "invalid_output", known, 200, true},
		{"truncated", goodAnalysis, "length", "invalid_output", known, 200, true},
		{"missing_usage", goodAnalysis, "stop", "usage_unknown", nil, 200, false},
		{"bad_cache", goodAnalysis, "stop", "usage_unknown", map[string]int64{"prompt_tokens": 10, "prompt_cache_hit_tokens": 20, "completion_tokens": 200}, 200, false},
		{"over_limit", goodAnalysis, "stop", "reservation_exceeded", map[string]int64{"prompt_tokens": 100, "completion_tokens": 5000}, 200, true},
		{"unauthorized", "", "", "http_error", nil, 401, false},
		{"rate_limited", "", "", "http_error", nil, 429, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a, id := analysisFixture(t)
			key := queueFixture(t, a, id)
			a.AIClient = analysisClient(tc.content, tc.finish, tc.usage, tc.code)
			if err := a.ProcessAnalysis(context.Background()); err != nil {
				t.Fatal(err)
			}
			j, err := a.Store.Analysis(context.Background(), key)
			if err != nil || j.Status != tc.want || ((j.Result != nil) != (tc.want == "succeeded")) {
				t.Fatalf("job %+v, %v", j, err)
			}
			var charged bool
			var pricing string
			if err = a.Store.DB.QueryRow(`SELECT cost_upper_nano IS NOT NULL,pricing FROM ai_calls`).Scan(&charged, &pricing); err != nil {
				t.Fatal(err)
			}
			if charged != tc.charged || strings.Contains(pricing, a.AIConfig.Key) {
				t.Fatal("cost unknown or secret leaked", pricing)
			}
			var began, ended int64
			if err = a.Store.DB.QueryRow(`SELECT started_ms,completed_ms FROM ai_call_timings`).Scan(&began, &ended); err != nil || began <= 0 || ended < began {
				t.Fatal("request interval missing", began, ended, err)
			}
			if err = a.ProcessAnalysis(context.Background()); err != nil {
				t.Fatal(err)
			}
			var calls int
			_ = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM ai_calls`).Scan(&calls)
			if calls != 1 {
				t.Fatal("automatically retried paid request")
			}
		})
	}
}

func TestAnalysisHTTPWakesWorkerAndRetainsTiming(t *testing.T) {
	a, id := analysisFixture(t)
	entered, release := make(chan struct{}), make(chan struct{})
	response := analysisClient(goodAnalysis, "stop", map[string]int64{"prompt_tokens": 100, "completion_tokens": 200}, 200)
	a.AIClient = &http.Client{Transport: probeTransport(func(r *http.Request) (*http.Response, error) {
		close(entered)
		select {
		case <-release:
		case <-r.Context().Done():
			return nil, r.Context().Err()
		}
		return response.Transport.RoundTrip(r)
	})}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { defer close(done); a.runAnalysis(ctx) }()
	defer func() { cancel(); <-done }()
	w := perform(a, authed(t, a, "POST", "news/"+strconv.FormatInt(id, 10)+"/analysis", `{"retry":false}`))
	if w.Code != 202 {
		t.Fatal(w.Code, w.Body.String())
	}
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("manual request waited for the five-second scheduler")
	}
	var completed int64
	if err := a.Store.DB.QueryRow(`SELECT completed_ms FROM ai_call_timings`).Scan(&completed); err != nil || completed != 0 {
		t.Fatal("unfinished request claimed a completed interval", completed, err)
	}
	if err := a.Store.SaveAISettings(ctx, AISettings{}, time.Now().Unix()); err != nil {
		t.Fatal(err)
	}
	close(release)
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		j, err := a.Store.NewsAnalysis(ctx, id)
		if err != nil {
			t.Fatal(err)
		}
		if j.Status == "succeeded" {
			cancel()
			<-done
			if err := initAnalysis(a.Store.DB); err != nil {
				t.Fatal(err)
			}
			var n int
			if err := a.Store.DB.QueryRow(`SELECT COUNT(*) FROM ai_call_timings WHERE completed_ms>=started_ms AND started_ms>0`).Scan(&n); err != nil || n != 1 {
				t.Fatal("completed request timing lost across initialization", n, err)
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("claimed request did not finish after pausing future work")
}
func TestAnalysisBudgetUnifiesProbeAndBusiness(t *testing.T) {
	a, id := analysisFixture(t)
	key := queueFixture(t, a, id)
	r := probeFixture()
	if ok, err := a.reserveAIProbe(r); err != nil || !ok {
		t.Fatal(ok, err)
	}
	_, err := a.Store.DB.Exec(`UPDATE ai_probe_runs SET reserved_nano=?`, monthlyBudgetNano)
	if err != nil {
		t.Fatal(err)
	}
	a.AIClient = &http.Client{Transport: probeTransport(func(*http.Request) (*http.Response, error) {
		t.Fatal("exhausted budget sent a request")
		return nil, nil
	})}
	if err = a.ProcessAnalysis(context.Background()); err != nil {
		t.Fatal(err)
	}
	j, _ := a.Store.Analysis(context.Background(), key)
	if j.Status != "budget_wait" {
		t.Fatal(j.Status)
	}
	_, err = a.Store.DB.Exec(`DELETE FROM ai_probe_timings; DELETE FROM ai_probe_runs; INSERT INTO ai_calls(job_id,month,started,status,reserved_nano,pricing) VALUES(?,strftime('%Y-%m','now','+8 hours'),0,'uncertain',?,'{}')`, key, monthlyBudgetNano)
	if err != nil {
		t.Fatal(err)
	}
	if ok, err := a.reserveAIProbe(r); err != nil || ok {
		t.Fatal("probe ignored business reservations", ok, err)
	}
	v, err := a.AIOverview(context.Background())
	if err != nil || v["remaining_nano"] != int64(0) {
		t.Fatal(v, err)
	}
}
func TestAnalysisSingleFlightCacheAndRestart(t *testing.T) {
	a, id := analysisFixture(t)
	key := queueFixture(t, a, id)
	again := queueFixture(t, a, id)
	if key != again {
		t.Fatal("cache key changed")
	}
	var count atomic.Int32
	started := make(chan struct{})
	release := make(chan struct{})
	a.AIClient = &http.Client{Transport: probeTransport(func(r *http.Request) (*http.Response, error) {
		count.Add(1)
		close(started)
		<-release
		if r.URL.String() != deepseekEndpoint || r.Header.Get("Authorization") != "Bearer "+a.AIConfig.Key {
			t.Error("wrong endpoint/key")
		}
		var request map[string]any
		_ = json.NewDecoder(r.Body).Decode(&request)
		if request["max_tokens"] != float64(analysisOutputTokens) || request["tools"] != nil {
			t.Error("unbounded request")
		}
		return analysisClient(goodAnalysis, "stop", map[string]int64{"prompt_tokens": 100, "completion_tokens": 200}, 200).Transport.RoundTrip(r)
	})}
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		if err := a.ProcessAnalysis(context.Background()); err != nil {
			t.Error(err)
		}
	}()
	<-started
	if err := a.ProcessAnalysis(context.Background()); err != nil {
		t.Fatal(err)
	}
	// A network call holds no database transaction and does not block ordinary reads.
	if _, err := a.Store.NewsDetail(context.Background(), id); err != nil {
		t.Fatal(err)
	}
	if a.aiProbeMu.TryLock() {
		a.aiProbeMu.Unlock()
		t.Fatal("probe slot not held")
	}
	close(release)
	wg.Wait()
	if count.Load() != 1 {
		t.Fatal(count.Load())
	}
	queueFixture(t, a, id)
	if err := a.ProcessAnalysis(context.Background()); err != nil {
		t.Fatal(err)
	}
	if count.Load() != 1 {
		t.Fatal("cache caused a request")
	}
	_, err := a.Store.DB.Exec(`UPDATE ai_jobs SET status='running'; UPDATE ai_calls SET status='running',cost_upper_nano=NULL`)
	if err != nil {
		t.Fatal(err)
	}
	if err = initAnalysis(a.Store.DB); err != nil {
		t.Fatal(err)
	}
	j, _ := a.Store.Analysis(context.Background(), key)
	if j.Status != "uncertain" {
		t.Fatal(j.Status)
	}
	if err = a.ProcessAnalysis(context.Background()); err != nil {
		t.Fatal(err)
	}
	if count.Load() != 1 {
		t.Fatal("restart repeated uncertain request")
	}
}
func TestAnalysisAutoScopeRevisionAndRetention(t *testing.T) {
	a, id := analysisFixture(t)
	ctx := context.Background()
	now := time.Now().Unix()
	if err := a.Store.SaveAISettings(ctx, AISettings{Enabled: true, Automatic: true}, now); err != nil {
		t.Fatal(err)
	}
	if err := a.Store.PlanAnalysis(ctx, a.AIConfig, now); err != nil {
		t.Fatal(err)
	}
	var n int
	_ = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM ai_jobs`).Scan(&n)
	if n != 0 {
		t.Fatal("historical backfill billed")
	}
	key := queueFixture(t, a, id)
	_, _, err := a.Store.SaveItems([]Item{{Source: "wallstreetcn", SourceID: "analysis-test", Title: "央行宣布降准", Content: "修订：幅度待确认", URL: "https://example.org/analysis", PublishedAt: now}}, now+1)
	if err != nil {
		t.Fatal(err)
	}
	stale, err := a.Store.NewsAnalysis(ctx, id)
	if err != nil || !stale.Stale {
		t.Fatal(stale, err)
	}
	_, err = a.Store.DB.Exec(`UPDATE news_features SET importance=3 WHERE news_id=?`, id)
	if err != nil {
		t.Fatal(err)
	}
	if err = a.Store.PlanAnalysis(ctx, a.AIConfig, now+2); err != nil {
		t.Fatal(err)
	}
	_ = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM ai_jobs`).Scan(&n)
	if n != 2 {
		t.Fatal("revision did not enqueue", n)
	}
	_, err = a.Store.DB.Exec(`UPDATE news SET last_seen=?`, now-91*86400)
	if err != nil {
		t.Fatal(err)
	}
	if err = a.Store.Prune(time.Now()); err != nil {
		t.Fatal(err)
	}
	j, err := a.Store.Analysis(ctx, key)
	if err != nil || len(j.Evidence) == 0 || !strings.Contains(j.Evidence[0].Text, "0.5") {
		t.Fatal("cited evidence lost", j, err)
	}
	_, err = a.Store.DB.Exec(`INSERT INTO ai_calls(job_id,month,started,status,reserved_nano,pricing) VALUES(?,'2026-11',0,'uncertain',123,'{}'); UPDATE ai_jobs SET expires=?`, key, now-1)
	if err != nil {
		t.Fatal(err)
	}
	if err = a.Store.Prune(time.Now()); err != nil {
		t.Fatal(err)
	}
	_ = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM ai_calls`).Scan(&n)
	if n != 1 {
		t.Fatal("retention released budget")
	}
}
func TestAnalysisPriceWindows(t *testing.T) {
	c := DefaultAIConfig()
	zone, _ := time.LoadLocation("Asia/Shanghai")
	for _, tc := range []struct {
		stamp string
		secs  int
		input int64
		basis string
	}{
		{"2026-11-01 10:00:00", 20, 1000, "off_peak_estimate"},
		{"2026-11-02 10:00:00", 20, 2000, "peak_upper"},
		{"2026-11-02 08:59:50", 20, 2000, "peak_upper"},
		{"2026-11-02 12:00:00", 20, 1000, "off_peak_estimate"},
	} {
		start, _ := time.ParseInLocation("2006-01-02 15:04:05", tc.stamp, zone)
		p, b := c.priceFor(start, start.Add(time.Duration(tc.secs)*time.Second))
		if p.Input != tc.input || b != tc.basis {
			t.Fatal(tc, p, b)
		}
	}
	c.HolidayYear = 2026
	c.Holidays = []string{"2026-11-02"}
	start, _ := time.ParseInLocation("2006-01-02 15:04:05", "2026-11-02 10:00:00", zone)
	p, _ := c.priceFor(start, start.Add(time.Second))
	if p.Input != 1000 {
		t.Fatal(p)
	}
}
func TestAnalysisHTTPAndConfigSafety(t *testing.T) {
	a, id := analysisFixture(t)
	if w := perform(a, httptest.NewRequest("GET", prefix+"ai", nil)); w.Code != 401 {
		t.Fatal(w.Code)
	}
	w := perform(a, authed(t, a, "GET", "ai", ""))
	if w.Code != 200 || strings.Contains(w.Body.String(), a.AIConfig.Key) {
		t.Fatal(w.Body.String())
	}
	path := "news/" + strconv.FormatInt(id, 10) + "/analysis"
	w = perform(a, authed(t, a, "POST", path, `{"retry":false}`))
	if w.Code != 202 {
		t.Fatal(w.Code, w.Body.String())
	}
	w = perform(a, authed(t, a, "GET", path, ""))
	if w.Code != 200 || !strings.Contains(w.Body.String(), "queued") {
		t.Fatal(w.Body.String())
	}
	r := authed(t, a, "PUT", "ai/settings", `{"enabled":true,"automatic":true}`)
	r.Header.Set("Origin", "https://evil.test")
	if w = perform(a, r); w.Code != 403 {
		t.Fatal(w.Code)
	}
	a.AIConfig = AIConfig{}
	w = perform(a, authed(t, a, "PUT", "ai/settings", `{"enabled":true,"automatic":true}`))
	if w.Code != 409 {
		t.Fatal(w.Code)
	}
	w = perform(a, authed(t, a, "PUT", "ai/settings", `{"enabled":false,"automatic":false}`))
	if w.Code != 200 {
		t.Fatal(w.Code)
	}
	dir := t.TempDir()
	if c, err := LoadAIConfig(dir); err != nil || c.Key != "" {
		t.Fatal(c, err)
	}
	if err := os.WriteFile(filepath.Join(dir, "ai-config.json"), []byte(`{"api_key":"do-not-log-this"`), 0600); err != nil {
		t.Fatal(err)
	}
	_, err := LoadAIConfig(dir)
	if err == nil || strings.Contains(err.Error(), "do-not-log-this") {
		t.Fatal(err)
	}
}

func TestAnalysisCancellationAndCacheAfterReversion(t *testing.T) {
	a, id := analysisFixture(t)
	key := queueFixture(t, a, id)
	a.AIClient = &http.Client{Transport: probeTransport(func(r *http.Request) (*http.Response, error) { return nil, context.DeadlineExceeded })}
	if err := a.ProcessAnalysis(context.Background()); err != nil {
		t.Fatal(err)
	}
	j, err := a.Store.Analysis(context.Background(), key)
	if err != nil || j.Status != "request_failed" || len(j.Calls) != 1 || j.Calls[0].Estimated != nil {
		t.Fatal(j, err)
	}
	// Restore the same bounded input after an intervening revision: reuse the exact cache,
	// retaining its original citation snapshot rather than claiming a new model invocation.
	old, err := a.Store.NewsDetail(context.Background(), id)
	if err != nil {
		t.Fatal(err)
	}
	modified := old.Item.Item
	modified.Content = "另一版本"
	now := time.Now().Unix()
	if _, _, err = a.Store.SaveItems([]Item{modified}, now+1); err != nil {
		t.Fatal(err)
	}
	if _, _, err = a.Store.SaveItems([]Item{old.Item.Item}, now+2); err != nil {
		t.Fatal(err)
	}
	retryKey, err := a.Store.QueueAnalysis(context.Background(), id, a.AIConfig, true, now+3)
	if err != nil || retryKey != key {
		t.Fatal(retryKey, err)
	}
	_, err = a.Store.DB.Exec(`UPDATE ai_settings SET not_before=0`)
	if err != nil {
		t.Fatal(err)
	}
	a.AIClient = analysisClient(goodAnalysis, "stop", map[string]int64{"prompt_tokens": 100, "completion_tokens": 200}, 200)
	if err = a.ProcessAnalysis(context.Background()); err != nil {
		t.Fatal(err)
	}
	current, err := a.Store.NewsAnalysis(context.Background(), id)
	if err != nil || current.Stale || current.Status != "succeeded" || current.Attempts != 2 {
		t.Fatal(current, err)
	}
}
func TestAnalysisSupersededAndConfigChangeDoNotCall(t *testing.T) {
	a, id := analysisFixture(t)
	key := queueFixture(t, a, id)
	a.AIClient = &http.Client{Transport: probeTransport(func(*http.Request) (*http.Response, error) { t.Fatal("stale task sent to model"); return nil, nil })}
	if _, err := a.Store.DB.Exec(`UPDATE news SET content='new evidence',version=2 WHERE id=?`, id); err != nil {
		t.Fatal(err)
	}
	if err := a.ProcessAnalysis(context.Background()); err != nil {
		t.Fatal(err)
	}
	j, err := a.Store.Analysis(context.Background(), key)
	if err != nil || j.Status != "superseded" || !j.Stale {
		t.Fatal(j, err)
	}
	fresh := queueFixture(t, a, id)
	a.AIConfig.Model = "another-model"
	if err = a.ProcessAnalysis(context.Background()); err != nil {
		t.Fatal(err)
	}
	j, err = a.Store.Analysis(context.Background(), fresh)
	if err != nil || j.Status != "config_changed" {
		t.Fatal(j, err)
	}
}
func TestAnalysisIdenticalCrossSourceKeepsOriginalCitations(t *testing.T) {
	a, id := analysisFixture(t)
	first := queueFixture(t, a, id)
	old, err := a.Store.NewsDetail(context.Background(), id)
	if err != nil {
		t.Fatal(err)
	}
	other := old.Item.Item
	other.Source = "trendradar:cls-telegraph"
	other.SourceID = "duplicate"
	if _, _, err = a.Store.SaveItems([]Item{other}, time.Now().Unix()); err != nil {
		t.Fatal(err)
	}
	var secondID int64
	if err = a.Store.DB.QueryRow(`SELECT id FROM news WHERE source_id='duplicate'`).Scan(&secondID); err != nil {
		t.Fatal(err)
	}
	if second := queueFixture(t, a, secondID); second != first {
		t.Fatal("identical inputs charged separately", first, second)
	}
	j, err := a.Store.NewsAnalysis(context.Background(), secondID)
	if err != nil || len(j.Evidence) != 1 || j.Evidence[0].Source != "wallstreetcn" {
		t.Fatal("cache rewrote evidence provenance", j, err)
	}
}
