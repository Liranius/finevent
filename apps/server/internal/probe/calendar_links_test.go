package probe

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"finevent/internal/calendar"
)

func linkEvent(t *testing.T, s *Store, key string) calendar.Event {
	t.Helper()
	e, err := (&calendar.Store{DB: s.DB}).Save(calendar.Event{Source: "manual", SourceID: key, Title: "测试美国 CPI 发布 " + key, Kind: "other", Start: "2026-11-05", End: "2026-11-05", Precision: "date", Timezone: "Asia/Shanghai", Status: "confirmed", Importance: 2, URL: "https://example.org/calendar", Evidence: "测试日程原文，日期明确。"}, 0)
	if err != nil {
		t.Fatal(err)
	}
	return e
}
func linkNews(t *testing.T, s *Store, key string) NewsEntry {
	t.Helper()
	saveNews(t, s, []Item{{Source: "test", SourceID: key, Title: "测试美国 CPI 报道 " + key, Content: "测试正文", URL: "https://example.org/news/" + key}}, time.Now().Unix())
	var id int64
	if err := s.DB.QueryRow(`SELECT id FROM news WHERE source='test' AND source_id=?`, key).Scan(&id); err != nil {
		t.Fatal(err)
	}
	d, err := s.NewsDetail(context.Background(), id)
	if err != nil {
		t.Fatal(err)
	}
	return d.Item
}
func TestCalendarLinksVersionsSnapshotsAndConflicts(t *testing.T) {
	a := testApp(t)
	s := a.Store
	ctx := context.Background()
	n := linkNews(t, s, "one")
	e := linkEvent(t, s, "one")
	in := CalendarLinkInput{NewsVersion: 1, EventVersion: 1, Note: "报道说明本次美国 CPI 发布，人工核对。"}
	if err := s.SaveCalendarLink(ctx, n.ID, e.ID, in, false); err != nil {
		t.Fatal(err)
	}
	page, err := s.CalendarLinks(ctx, n.ID, "", 0)
	if err != nil || len(page.Items) != 1 || page.Items[0].Changed || page.Items[0].NewsSnapshot.Content != "测试正文" {
		t.Fatalf("%+v %v", page, err)
	}
	link := page.Items[0]
	if err = s.SaveCalendarLink(ctx, n.ID, e.ID, in, false); !errors.Is(err, calendar.ErrConflict) {
		t.Fatal("duplicate overwrote confirmation", err)
	}
	e.Start, e.End = "2026-11-06", "2026-11-06"
	e, err = (&calendar.Store{DB: s.DB}).Save(e, e.Version)
	if err != nil {
		t.Fatal(err)
	}
	page, err = s.CalendarLinks(ctx, 0, e.ID, 0)
	if err != nil || !page.Items[0].Changed || page.Items[0].EventSnapshot.Start != "2026-11-05" || page.Items[0].Event.Start != "2026-11-06" {
		t.Fatalf("%+v %v", page, err)
	}
	in.Revision = link.ID
	if err = s.SaveCalendarLink(ctx, n.ID, e.ID, in, false); !errors.Is(err, calendar.ErrConflict) {
		t.Fatal("stale event confirmed", err)
	}
	in.EventVersion = e.Version
	saveNews(t, s, []Item{{Source: "test", SourceID: "one", Title: "修订报道", Content: "修订正文"}}, time.Now().Unix())
	if err = s.SaveCalendarLink(ctx, n.ID, e.ID, in, false); !errors.Is(err, calendar.ErrConflict) {
		t.Fatal("stale news confirmed", err)
	}
	in.NewsVersion = 2
	if err = s.SaveCalendarLink(ctx, n.ID, e.ID, in, false); err != nil {
		t.Fatal(err)
	}
	page, _ = s.CalendarLinks(ctx, n.ID, "", 0)
	if page.Items[0].Changed || page.Items[0].ID == link.ID || page.Items[0].NewsSnapshot.Content != "修订正文" {
		t.Fatal(page)
	}
	if err = s.SaveCalendarLink(ctx, n.ID, e.ID, in, true); !errors.Is(err, calendar.ErrConflict) {
		t.Fatal("old confirmation removed newer one", err)
	}
	in.Revision = page.Items[0].ID
	if err = s.SaveCalendarLink(ctx, n.ID, e.ID, in, true); err != nil {
		t.Fatal(err)
	}
	in.Revision = 0
	if err = s.SaveCalendarLink(ctx, n.ID, e.ID, in, false); err != nil {
		t.Fatal(err)
	}
	page, _ = s.CalendarLinks(ctx, n.ID, "", 0)
	if page.Items[0].ID <= link.ID {
		t.Fatal("identity reused")
	}
}

func TestCalendarLinksRestartPaginationAndRetention(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.DB.Close() })
	ctx := context.Background()
	e := linkEvent(t, s, "many")
	for i := 0; i < 32; i++ {
		n := linkNews(t, s, fmt.Sprint(i))
		if err = s.SaveCalendarLink(ctx, n.ID, e.ID, CalendarLinkInput{NewsVersion: 1, EventVersion: 1, Note: "测试人工核对关联依据"}, false); err != nil {
			t.Fatal(err)
		}
	}
	s.DB.Close()
	s, err = OpenStore(filepath.Clean(dir))
	if err != nil {
		t.Fatal(err)
	}
	page, err := s.CalendarLinks(ctx, 0, e.ID, 0)
	if err != nil || len(page.Items) != 30 || page.Next == 0 {
		t.Fatalf("%+v %v", page, err)
	}
	second, err := s.CalendarLinks(ctx, 0, e.ID, page.Next)
	if err != nil || len(second.Items) != 2 || second.Next != 0 {
		t.Fatalf("%+v %v", second, err)
	}
	if _, err = s.DB.Exec(`DELETE FROM news WHERE id=?`, page.Items[0].News.ID); err != nil {
		t.Fatal(err)
	}
	if _, err = s.CalendarLinks(ctx, page.Items[0].News.ID, "", 0); !errors.Is(err, sql.ErrNoRows) {
		t.Fatal(err)
	}
	var count int
	if err = s.DB.QueryRow(`SELECT count(*) FROM news_calendar_links`).Scan(&count); err != nil || count != 31 {
		t.Fatal(count, err)
	}
	if _, err = (&calendar.Store{DB: s.DB}).Get(e.ID); err != nil {
		t.Fatal("news cleanup removed calendar", err)
	}
}

func TestCalendarLinkSearchBoundsAndPrecision(t *testing.T) {
	a := testApp(t)
	s := a.Store
	ctx := context.Background()
	for i := 0; i < 32; i++ {
		linkEvent(t, s, fmt.Sprint(i))
	}
	page, err := s.SearchCalendarLinks(ctx, "2026-11-05", "2026-11-05", "CPI")
	if err != nil || len(page.Items) != 30 || !page.More || page.Items[0].Precision != "date" {
		t.Fatalf("%+v %v", page, err)
	}
	page, err = s.SearchCalendarLinks(ctx, "2026-11-06", "2026-11-06", "")
	if err != nil || len(page.Items) != 0 {
		t.Fatal(page, err)
	}
	e := linkEvent(t, s, "DST")
	e.Precision = "minute"
	e.Timezone = "America/New_York"
	e.Start = "2026-11-05T20:30:00-05:00"
	e.End = e.Start
	if _, err = (&calendar.Store{DB: s.DB}).Save(e, e.Version); err != nil {
		t.Fatal(err)
	}
	page, err = s.SearchCalendarLinks(ctx, "2026-11-06", "2026-11-06", "DST")
	if err != nil || len(page.Items) != 1 {
		t.Fatal(page, err)
	}
	if _, err = s.SearchCalendarLinks(ctx, "2026-11-06", "2026-11-05", ""); !errors.Is(err, errLinkInput) {
		t.Fatal(err)
	}
	if _, err = s.SearchCalendarLinks(ctx, "2026-11-05", "2026-11-05", strings.Repeat("字", 101)); !errors.Is(err, errLinkInput) {
		t.Fatal(err)
	}
}

func TestCalendarLinkRoutesSecurityAndErrors(t *testing.T) {
	a := testApp(t)
	n := linkNews(t, a.Store, "http")
	e := linkEvent(t, a.Store, "http")
	path := fmt.Sprintf("news/%d/calendar-links/%s", n.ID, e.ID)
	body := `{"revision":0,"news_version":1,"event_version":1,"note":"测试人工确认关联依据"}`
	if w := perform(a, httptest.NewRequest("GET", prefix+"calendar-link-search", nil)); w.Code != 401 {
		t.Fatal(w.Code)
	}
	r := authed(t, a, "PUT", path, body)
	r.Header.Set("Origin", "https://evil.test")
	if w := perform(a, r); w.Code != 403 {
		t.Fatal(w.Code)
	}
	if w := perform(a, authed(t, a, "PUT", path, body)); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w := perform(a, authed(t, a, "PUT", path, body)); w.Code != 409 {
		t.Fatal(w.Code, w.Body.String())
	}
	for _, path := range []string{fmt.Sprintf("news/%d/calendar-links", n.ID), "calendar/" + e.ID + "/news-links", "calendar-link-search?from=2026-11-05&to=2026-11-05"} {
		w := perform(a, authed(t, a, "GET", path, ""))
		if w.Code != 200 || !strings.Contains(w.Header().Get("Cache-Control"), "no-store") {
			t.Fatal(path, w.Code, w.Body.String())
		}
		var out map[string]any
		if json.Unmarshal(w.Body.Bytes(), &out) != nil || len(out["items"].([]any)) != 1 {
			t.Fatal(w.Body.String())
		}
	}
	for path, code := range map[string]int{"news/x/calendar-links": 400, "news/1/calendar-links?before=bad": 400, "calendar-link-search?from=bad&to=bad": 400, "calendar/missing/news-links": 404} {
		if w := perform(a, authed(t, a, "GET", path, "")); w.Code != code {
			t.Fatal(path, w.Code, w.Body.String())
		}
	}
}
