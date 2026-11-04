package calendar

import (
	"context"
	"database/sql"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	_ "modernc.org/sqlite"
	_ "time/tzdata"
)

func testStore(t *testing.T) *Store {
	t.Helper()
	db, err := sql.Open("sqlite", filepath.Join(t.TempDir(), "events.db"))
	if err != nil {
		t.Fatal(err)
	}
	db.SetMaxOpenConns(1)
	t.Cleanup(func() { db.Close() })
	if err = Init(db); err != nil {
		t.Fatal(err)
	}
	return &Store{DB: db}
}
func sample() Event {
	return Event{Source: "manual", SourceID: "one", Title: "官方日程", Kind: "other", Start: "2026-11-01", End: "2026-11-03", Timezone: "America/New_York", Precision: "date", Status: "scheduled", Importance: 2, URL: "https://example.org/notice", Evidence: "原始依据"}
}
func TestPrecisionDSTAndRange(t *testing.T) {
	summer, err := localTime("2026-10-30", "08:30", "America/New_York")
	if err != nil || summer != "2026-10-30T08:30:00-04:00" {
		t.Fatal(summer, err)
	}
	winter, err := localTime("2026-11-06", "08:30", "America/New_York")
	if err != nil || winter != "2026-11-06T08:30:00-05:00" {
		t.Fatal(winter, err)
	}
	e := sample()
	if err = e.Validate(); err != nil {
		t.Fatal(err)
	}
	start, end := e.Days()
	if start != "2026-11-01" || end != "2026-11-03" {
		t.Fatal("civil dates shifted", start, end)
	}
	e.Precision = "minute"
	e.Start = "2026-11-06T20:00:00-05:00"
	e.End = e.Start
	if err = e.Validate(); err != nil {
		t.Fatal(err)
	}
	start, _ = e.Days()
	if start != "2026-11-07" {
		t.Fatal(start)
	}
	e.Start = "2026-11-06T08:30:00-04:00"
	e.End = e.Start
	if e.Validate() == nil {
		t.Fatal("incorrect DST offset accepted")
	}
	if DateRange("2026-02-30", "2026-03-01") == nil || DateRange("2026-01-01", "2028-01-01") == nil {
		t.Fatal("invalid range accepted")
	}
}
func TestRevisionsOverlapAndConflict(t *testing.T) {
	s := testStore(t)
	e, err := s.Save(sample(), 0)
	if err != nil {
		t.Fatal(err)
	}
	old := e
	if _, err = s.Save(old, 0); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	list, err := s.List("2026-11-02", "2026-11-02")
	if err != nil || len(list) != 1 {
		t.Fatal(list, err)
	}
	e.Start = "2026-11-02"
	e, err = s.Save(e, 1)
	if err != nil || e.Status != "rescheduled" || e.Version != 2 {
		t.Fatal(e, err)
	}
	e.Status = "scheduled"
	e, err = s.Save(e, -1)
	if err != nil || e.Version != 2 {
		t.Fatal("unchanged sync creates revision", e, err)
	}
	e.Status = "cancelled"
	e, err = s.Save(e, 2)
	if err != nil || e.Version != 3 {
		t.Fatal(e, err)
	}
	history, err := s.History(e.ID)
	if err != nil || len(history) != 3 || history[2].Event.Start != "2026-11-01" || history[0].Event.Status != "cancelled" {
		t.Fatal(history, err)
	}
	if err = Init(s.DB); err != nil {
		t.Fatal(err)
	}
	again, err := s.Get(e.ID)
	if err != nil || again.Version != 3 {
		t.Fatal("migration lost data")
	}
}
func TestSourceFailureIsVisibleAndDoesNotCancelMissing(t *testing.T) {
	s := testStore(t)
	e := sample()
	e.Source = "nbs"
	if err := s.Import("nbs", []Event{e}, 100, nil); err != nil {
		t.Fatal(err)
	}
	if err := s.Import("nbs", nil, 200, errors.New("HTTP 403")); err != nil {
		t.Fatal(err)
	}
	sources, err := s.Sources()
	if err != nil || sources[0].LastSuccess != 100 || sources[0].LastAttempt != 200 || sources[0].Error != "HTTP 403" {
		t.Fatal(sources, err)
	}
	e.SourceID = "new"
	if err = s.Import("nbs", []Event{e}, 300, nil); err != nil {
		t.Fatal(err)
	}
	list, err := s.List("2026-11-01", "2026-11-03")
	if err != nil || len(list) != 2 || list[0].Status == "cancelled" {
		t.Fatal("missing rows were deleted or cancelled", list, err)
	}
	bad := e
	bad.SourceID = "invalid"
	bad.Start = "wrong"
	if s.Import("nbs", []Event{e, bad}, 400, nil) == nil {
		t.Fatal("partial invalid import accepted")
	}
}
func TestFundDraftNeedsConfirmationAndKeepsEvidence(t *testing.T) {
	s := testStore(t)
	text := "基金代码：123456。开放赎回时间为2026年11月2日至2026年11月6日。仅限交易日，每日限额10000元。"
	d := ExtractFund("draft", text, "https://example.org/fund")
	if d.Event.FundCode != "123456" || d.Event.Start != "2026-11-02" || d.Event.End != "2026-11-06" {
		t.Fatal(d)
	}
	if err := s.SaveDraft(d); err != nil {
		t.Fatal(err)
	}
	list, _ := s.List("2026-11-01", "2026-11-30")
	if len(list) != 0 {
		t.Fatal("draft became event")
	}
	e, err := s.Confirm(d.ID, d.Event)
	if err != nil || e.Status != "confirmed" || e.Evidence != text {
		t.Fatal(e, err)
	}
	second := d.Event
	second.Start = "2026-11-03"
	again, err := s.Confirm(d.ID, second)
	if err != nil || again.ID != e.ID || again.Start != e.Start {
		t.Fatal("duplicate confirm changed event", again, err)
	}
	ambiguous := ExtractFund("two", "基金代码123456；基金代码654321。下月开放，封闭期六个月。", "https://example.org/a")
	if ambiguous.Event.Start != "" || ambiguous.Event.FundCode != "" {
		t.Fatal("inferred ambiguous values", ambiguous)
	}
	d = ExtractFund("bad", "公告日2026年10月20日，下次开放由管理人另行公告，基金代码123456。", "https://example.org/a")
	if d.Event.Start != "" {
		t.Fatal("publication date used as window")
	}
}
func fixture(t *testing.T, name string) string {
	t.Helper()
	b, err := os.ReadFile("testdata/" + name)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}
func TestOfficialSnapshots(t *testing.T) {
	for _, tc := range []struct {
		source, file string
		count        int
	}{{"nbs", "nbs.html", 47}, {"fomc", "fomc.txt", 8}, {"bea", "bea.html", 11}} {
		t.Run(tc.source, func(t *testing.T) {
			events, err := Parse(tc.source, "https://example.org/schedule", fixture(t, tc.file), 2026)
			if err != nil || len(events) != tc.count {
				t.Fatal(len(events), err)
			}
			if tc.source == "nbs" {
				marchPMI := 0
				for _, e := range events {
					if strings.Contains(e.Title, "采购经理") && strings.HasPrefix(e.Start, "2026-03-") {
						marchPMI++
					}
					if strings.Contains(e.Title, "国民经济") && strings.HasPrefix(e.Start, "2026-08") && e.Start != "2026-08-17T15:00:00+08:00" {
						t.Fatal(e)
					}
				}
				if marchPMI != 2 {
					t.Fatal("lost exceptional March PMI", marchPMI)
				}
			}
			if tc.source == "fomc" && (events[0].Start != "2026-01-27" || events[0].End != "2026-01-28" || events[0].Precision != "date") {
				t.Fatal("invented FOMC time", events[0])
			}
			if tc.source == "bea" && events[0].Start != "2026-11-04T08:30:00-05:00" {
				t.Fatal(events[0])
			}
		})
	}
}
func TestICSFoldingTimezoneAndCancellation(t *testing.T) {
	body := "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:stable-cpi\r\nDTSTART;TZID=America/New_York:20261030T083000\r\nSUMMARY:Consumer Price\r\n  Index\r\nSTATUS:CANCELLED\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
	events, err := Parse("bls", "https://www.bls.gov/schedule/news_release/bls.ics", body, 2026)
	if err != nil || len(events) != 1 {
		t.Fatal(events, err)
	}
	e := events[0]
	if e.Title != "Consumer Price Index" || e.Start != "2026-10-30T08:30:00-04:00" || e.Status != "cancelled" {
		t.Fatal(e)
	}
	if _, err = Parse("bls", e.URL, strings.Replace(body, "America/New_York", "Unknown/Zone", 1), 2026); err == nil {
		t.Fatal("unknown zone accepted")
	}
	if _, err = Parse("bls", e.URL, "<html>Access denied</html>", 2026); err == nil {
		t.Fatal("HTML accepted as ICS")
	}
}

type transport func(*http.Request) (*http.Response, error)

func (f transport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func TestDurableSixHourCadenceAndBodyLimits(t *testing.T) {
	s := testStore(t)
	calls := 0
	c := &http.Client{Transport: transport(func(r *http.Request) (*http.Response, error) {
		calls++
		return &http.Response{StatusCode: 403, Body: io.NopCloser(strings.NewReader("blocked")), Header: make(http.Header)}, nil
	})}
	if err := s.SyncDue(context.Background(), c); err != nil {
		t.Fatal(err)
	}
	if calls != len(Sources()) {
		t.Fatal(calls)
	}
	s = &Store{DB: s.DB}
	if err := s.SyncDue(context.Background(), c); err != nil || calls != len(Sources()) {
		t.Fatal("restart repeated polls", err, calls)
	}
	c.Transport = transport(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(strings.Repeat("x", 2*1024*1024+1)))}, nil
	})
	if _, err := Fetch(context.Background(), c, "https://example.org/a"); err == nil {
		t.Fatal("unbounded body accepted")
	}
}

func TestOfficialFetchAllowsSlowBodyWithinOneMinute(t *testing.T) {
	client := &http.Client{Timeout: time.Minute, Transport: transport(func(r *http.Request) (*http.Response, error) {
		deadline, ok := r.Context().Deadline()
		if !ok || time.Until(deadline) < 55*time.Second || time.Until(deadline) > time.Minute {
			t.Errorf("calendar request was shortened before reading the official page: %v", time.Until(deadline))
			return nil, context.DeadlineExceeded
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader("complete schedule"))}, nil
	})}
	if body, err := Fetch(context.Background(), client, "https://www.stats.gov.cn/schedule"); err != nil || body != "complete schedule" {
		t.Fatal(body, err)
	}
}

func TestOfficialBodyTimeoutHonorsParentAndPreservesSource(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "partial official page")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	body, fetchErr := Fetch(ctx, NewHTTPClient(), server.URL)
	if body != "" || fetchErr == nil || fetchErr.Error() != "读取官方日程超时" {
		t.Fatal("partial/expired response must not be usable", body, fetchErr)
	}
	s := testStore(t)
	e := sample()
	e.Source = "nbs"
	if err := s.Import("nbs", []Event{e}, 100, nil); err != nil {
		t.Fatal(err)
	}
	if err := s.Import("nbs", nil, 200, fetchErr); err != nil {
		t.Fatal(err)
	}
	sources, err := s.Sources()
	if err != nil {
		t.Fatal(err)
	}
	for _, source := range sources {
		if source.ID == "nbs" && (source.Count != 1 || source.LastSuccess != 100 || source.LastAttempt != 200 || source.Error != fetchErr.Error()) {
			t.Fatal("timeout replaced successful source data", source)
		}
	}
}
func TestAnnualDiscoveryDoesNotInventURLs(t *testing.T) {
	links, err := nbsLinks(`<a href="https://www.stats.gov.cn/2026.html">2026年国家统计局主要统计信息发布日程表</a><a href="https://evil.test/2027">2027年国家统计局主要统计信息发布日程表</a>`, 2026)
	if err != nil || len(links) != 1 || links[0] != "https://www.stats.gov.cn/2026.html" {
		t.Fatal(links, err)
	}
	if _, err = nbsLinks("no current annual schedule", 2027); err == nil {
		t.Fatal("missing annual schedule accepted")
	}
}

func TestNBSDisclosureMetadataAndICSDateInterval(t *testing.T) {
	body := strings.Replace(fixture(t, "nbs.html"), "<title>2026年国家统计局主要统计信息发布日程表</title>", `<title>国家统计局信息公开</title><meta name="ArticleTitle" content="2026年国家统计局主要统计信息发布日程表">`, 1)
	events, err := Parse("nbs", "https://www.stats.gov.cn/schedule", body, 2026)
	if err != nil || len(events) != 47 {
		t.Fatal(len(events), err)
	}
	ics := "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:interval\nSUMMARY:Schedule\nDTSTART;VALUE=DATE:20261101\nDTEND;VALUE=DATE:20261104\nEND:VEVENT\nEND:VCALENDAR"
	events, err = Parse("bls", "https://www.bls.gov/schedule", ics, 2026)
	if err != nil || events[0].End != "2026-11-03" {
		t.Fatal(events, err)
	}
}
