package probe

import (
	"net/http/httptest"
	"strings"
	"testing"
)

func TestTrendSettingsRequiresSessionAndOrigin(t *testing.T) {
	a := testApp(t)
	path := "news/trendradar-settings"
	if result := perform(a, httptest.NewRequest("GET", prefix+path, nil)); result.Code != 401 {
		t.Fatal(result.Code)
	}
	body := `{"section":"sources","revision":"` + strings.Repeat("a", 64) + `","value":[]}`
	request := authed(t, a, "PUT", path, body)
	request.Header.Set("Origin", "https://other.example")
	if result := perform(a, request); result.Code != 403 {
		t.Fatal(result.Code)
	}
	if result := perform(a, authed(t, a, "PUT", path, `{"section":"cron"}`)); result.Code != 400 {
		t.Fatal(result.Code)
	}
	if result := perform(a, authed(t, a, "GET", path, "")); result.Code != 503 || result.Header().Get("Cache-Control") != "no-store" {
		t.Fatal(result.Code)
	}
}

func TestConfiguredSourceHealthKeepsHistoryAndChecksUnseenSources(t *testing.T) {
	change := float64(2800)
	periods := []sourcePeriod{{Start: 0, End: &change, IDs: []string{"old", "unseen"}}, {Start: change, IDs: []string{"new", "unseen"}, Catalog: []string{"new", "old", "unseen", "off"}}}
	build := func(now, cycle int64) TrendHealth {
		h := TrendHealth{LatestCycle: cycle, ServerTime: now, Sources: []TrendSource{{ID: "old", State: "failed", LastFailure: 1800, Stale: true}}}
		applySourcePeriods(&h, periods)
		return h
	}
	h := build(3000, 1800)
	if h.Sources[0].State != "pending" || h.Sources[1].State != "disabled" || h.Sources[2].State != "disabled" || h.Sources[2].LastFailure != 1800 || h.Sources[3].State != "missing" {
		t.Fatal(h)
	}
	if h = build(3700, 3600); h.Sources[0].State != "missing" {
		t.Fatal(h)
	}
	h = TrendHealth{ServerTime: 3700, LatestCycle: 3600, Sources: []TrendSource{{ID: "old", State: "healthy", LastAttempt: 3600}}}
	applySourcePeriods(&h, periods)
	if h.Sources[2].State != "unexpected" {
		t.Fatal(h)
	}
}
