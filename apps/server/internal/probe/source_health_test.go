package probe

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func healthExec(t *testing.T, db *sql.DB, query string, args ...any) {
	t.Helper()
	if _, err := db.Exec(query, args...); err != nil {
		t.Fatal(err)
	}
}
func healthDay(t *testing.T, root, day string) *sql.DB {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(root, "news"), 0700); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite", filepath.Join(root, "news", day+".db"))
	if err != nil {
		t.Fatal(err)
	}
	db.SetMaxOpenConns(1)
	healthExec(t, db, `PRAGMA journal_mode=WAL; CREATE TABLE crawl_records(id INTEGER PRIMARY KEY,crawl_time TEXT); CREATE TABLE crawl_source_status(crawl_record_id INTEGER,platform_id TEXT,status TEXT);`)
	t.Cleanup(func() { db.Close() })
	return db
}
func healthCycle(t *testing.T, db *sql.DB, id int, clock string, statuses map[string]string) {
	t.Helper()
	healthExec(t, db, `INSERT INTO crawl_records VALUES(?,?)`, id, clock)
	for source, status := range statuses {
		healthExec(t, db, `INSERT INTO crawl_source_status VALUES(?,?,?)`, id, source, status)
	}
}
func saveHealth(t *testing.T, s *Store, cycles []trendCycle, now int64) {
	t.Helper()
	if err := s.saveTrendHealth(context.Background(), cycles, []string{}, now, nil); err != nil {
		t.Fatal(err)
	}
}
func getHealth(t *testing.T, s *Store, now int64) TrendHealth {
	t.Helper()
	h, err := s.TrendHealth(context.Background(), now)
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func TestSourceHealthReadOnlyMidnightAndConcurrentWAL(t *testing.T) {
	root := t.TempDir()
	zone, _ := time.LoadLocation("Asia/Shanghai")
	now := time.Date(2026, 11, 2, 0, 5, 0, 0, zone)
	yesterday := healthDay(t, root, "2026-11-01")
	today := healthDay(t, root, "2026-11-02")
	healthCycle(t, yesterday, 1, "23-30", map[string]string{"a": "failed"})
	healthCycle(t, today, 1, "00-01", map[string]string{"a": "success"})
	writer, err := today.Begin()
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Rollback()
	if _, err = writer.Exec(`UPDATE crawl_source_status SET status='failed'`); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	cycles, warnings, err := readTrendHealth(ctx, root, now)
	if err != nil || len(warnings) != 0 || len(cycles) != 2 || cycles[1].Sources[0].Status != "success" {
		t.Fatalf("snapshot: %+v %v %v", cycles, warnings, err)
	}
	if cycles[1].At != now.Add(-4*time.Minute).Unix() {
		t.Fatal("Beijing midnight conversion", cycles)
	}
	if err = writer.Commit(); err != nil {
		t.Fatal(err)
	}
	a := testApp(t)
	saveHealth(t, a.Store, cycles, now.Unix())
	saveHealth(t, a.Store, cycles, now.Unix()+1)
	h := getHealth(t, a.Store, now.Unix()+1)
	if h.Sources[0].RecoveredAt != cycles[1].At || h.Sources[0].State != "healthy" {
		t.Fatal(h)
	}
	var count int
	_ = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM trend_source_observations`).Scan(&count)
	if count != 2 {
		t.Fatal("repeat/cross-day identity duplicated", count)
	}
	cycles, _, err = readTrendHealth(ctx, root, now)
	if err != nil {
		t.Fatal(err)
	}
	saveHealth(t, a.Store, cycles, now.Unix()+2)
	h = getHealth(t, a.Store, now.Unix()+2)
	if h.Sources[0].State != "failed" || h.Sources[0].RecoveredAt != 0 {
		t.Fatal("revision not imported", h)
	}
	healthExec(t, today, `UPDATE crawl_source_status SET status='success'`)
	cycles, _, err = readTrendHealth(ctx, root, now)
	if err != nil {
		t.Fatal(err)
	}
	saveHealth(t, a.Store, cycles, now.Unix()+3)
	page, err := a.Store.TrendFailures(ctx, "a", 0, 0)
	if err != nil || len(page.Items) != 2 || !page.Items[0].Corrected || page.Items[0].RecoveredAt != 0 {
		t.Fatal("corrected failure erased or invented recovery", page, err)
	}
	var status string
	if err = today.QueryRow(`SELECT status FROM crawl_source_status`).Scan(&status); err != nil || status != "success" {
		t.Fatal("reader changed upstream", status, err)
	}
}

func TestSourceHealthMissingStaleReadErrorRestartAndRetention(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { s.DB.Close() }()
	now := time.Now().Unix()
	cycles := []trendCycle{{"old", 1, now - 31*86400, []trendStatus{{"old", "failed"}}}, {"today", 1, now - 1800, []trendStatus{{"a", "failed"}, {"b", "success"}}}, {"today", 2, now - 60, []trendStatus{{"a", "success"}, {"b", "success"}}}}
	saveHealth(t, s, cycles, now)
	if err = s.Prune(time.Unix(now, 0)); err != nil {
		t.Fatal(err)
	}
	if err = s.DB.Close(); err != nil {
		t.Fatal(err)
	}
	s, err = OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	h := getHealth(t, s, now)
	if len(h.Sources) != 2 || h.Sources[0].LastFailure != now-1800 || h.Sources[0].RecoveredAt != now-60 {
		t.Fatal("retention or restart", h)
	}
	// A later empty/partial cycle at the same minute must not borrow an earlier success.
	saveHealth(t, s, []trendCycle{{"today", 3, now - 60, []trendStatus{{"b", "success"}}}}, now)
	h = getHealth(t, s, now)
	if h.Sources[0].State != "missing" || h.Sources[1].State != "healthy" {
		t.Fatal(h)
	}
	if err = s.saveTrendHealth(context.Background(), nil, []string{}, now+1, errors.New("missing table")); err != nil {
		t.Fatal(err)
	}
	h = getHealth(t, s, now+1)
	if h.ReadError == "" || h.ReadSuccess != now || h.Sources[1].State != "unknown" {
		t.Fatal("read failure showed healthy", h)
	}
	h = getHealth(t, s, now+2500)
	if !h.ReadStale || !h.Sources[0].Stale || h.Sources[1].State != "stale" {
		t.Fatal(h)
	}
	var count int
	_ = s.DB.QueryRow(`SELECT COUNT(*) FROM trend_source_observations WHERE source='old'`).Scan(&count)
	if count != 0 {
		t.Fatal("orphaned expired statuses")
	}
}

func TestSourceHealthFailureHistoryPagingAndRecovery(t *testing.T) {
	a := testApp(t)
	now := time.Now().Unix()
	cycles := []trendCycle{}
	for i := 0; i < 36; i++ {
		cycles = append(cycles, trendCycle{"today", int64(i + 1), now - int64(40-i)*60, []trendStatus{{"a", "failed"}, {"b", "failed"}}})
	}
	cycles = append(cycles, trendCycle{"today", 37, now - 60, []trendStatus{{"a", "success"}, {"b", "failed"}}})
	saveHealth(t, a.Store, cycles, now)
	seen := map[int64]bool{}
	var at, id int64
	for {
		page, err := a.Store.TrendFailures(context.Background(), "a", at, id)
		if err != nil {
			t.Fatal(err)
		}
		for _, row := range page.Items {
			if row.Source != "a" || row.RecoveredAt != now-60 || seen[row.ID] {
				t.Fatal(row)
			}
			seen[row.ID] = true
		}
		if page.Next == "" {
			break
		}
		if _, err = fmt.Sscanf(page.Next, "%d:%d", &at, &id); err != nil {
			t.Fatal(err)
		}
	}
	if len(seen) != 36 {
		t.Fatal("lost failure history", len(seen))
	}
	page, err := a.Store.TrendFailures(context.Background(), "b", 0, 0)
	if err != nil || page.Items[0].RecoveredAt != 0 {
		t.Fatal(page, err)
	}
}

func TestSourceHealthSchemaFailureDoesNotStopNewsImport(t *testing.T) {
	a := testApp(t)
	root := t.TempDir()
	zone, _ := time.LoadLocation("Asia/Shanghai")
	now := time.Now().In(zone)
	db := healthDay(t, root, now.Format("2006-01-02"))
	healthExec(t, db, `DROP TABLE crawl_source_status; CREATE TABLE news_items(id INTEGER,title TEXT,platform_id TEXT,url TEXT,first_crawl_time TEXT,last_crawl_time TEXT); INSERT INTO news_items VALUES(1,'news','a','https://example.test/article','00-00','00-00')`)
	p := a.CollectTrend(root)
	if !p.Success || p.Added != 1 {
		t.Fatal("status failure interrupted news", p)
	}
	h := getHealth(t, a.Store, time.Now().Unix())
	if h.ReadError == "" || h.ReadSuccess != 0 || len(h.Sources) != 0 {
		t.Fatal(h)
	}
}

func TestSourceHealthUnknownIncompleteAndInvalidUpstream(t *testing.T) {
	root := t.TempDir()
	zone, _ := time.LoadLocation("Asia/Shanghai")
	now := time.Date(2026, 11, 2, 0, 1, 0, 0, zone)
	db := healthDay(t, root, "2026-11-01")
	healthCycle(t, db, 1, "23-30", map[string]string{"a": "unexpected"})
	cycles, warnings, err := readTrendHealth(context.Background(), root, now)
	if err != nil || len(warnings) != 1 || cycles[0].Sources[0].Status != "unknown" {
		t.Fatal(cycles, warnings, err)
	}
	healthExec(t, db, `INSERT INTO crawl_source_status VALUES(1,'a','success')`)
	if _, _, err = readTrendHealth(context.Background(), root, now); err == nil {
		t.Fatal("duplicate source accepted")
	}
	healthExec(t, db, `DELETE FROM crawl_source_status; UPDATE crawl_records SET crawl_time='not-a-time'`)
	if _, _, err = readTrendHealth(context.Background(), root, now); err == nil {
		t.Fatal("invalid time accepted")
	}
	if _, _, err = readTrendHealth(context.Background(), t.TempDir(), now); err == nil {
		t.Fatal("missing databases accepted")
	}
}

func TestSourceHealthAPISecurityAndValidation(t *testing.T) {
	a := testApp(t)
	for _, path := range []string{"news/source-health", "news/source-failures"} {
		if w := perform(a, httptest.NewRequest("GET", prefix+path, nil)); w.Code != 401 {
			t.Fatal(w.Code)
		}
		w := perform(a, authed(t, a, "GET", path, ""))
		if w.Code != 200 || !strings.Contains(w.Header().Get("Cache-Control"), "no-store") {
			t.Fatal(w.Code, w.Body.String())
		}
		if path == "news/source-health" && !strings.Contains(w.Body.String(), `"sources":[]`) {
			t.Fatal(w.Body.String())
		}
	}
	for _, cursor := range []string{"abc", "0:1", "1:0", "-1:1", "1:2:3"} {
		if w := perform(a, authed(t, a, "GET", "news/source-failures?before="+cursor, "")); w.Code != 400 {
			t.Fatal(cursor, w.Code)
		}
	}
	if w := perform(a, authed(t, a, "GET", "news/source-failures?source="+strings.Repeat("a", 129), "")); w.Code != 400 {
		t.Fatal(w.Code)
	}
}
