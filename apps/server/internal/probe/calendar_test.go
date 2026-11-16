package probe

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"finevent/internal/calendar"
)

func TestFollowedBenchmarkKeepsReminderScheduleAcrossSources(t *testing.T) {
	now := time.Date(2027, 8, 24, 0, 0, 0, 0, time.UTC)
	a, _, _ := reminderFixture(t, now)
	s := &calendar.Store{DB: a.Store.DB}
	e := calendar.Event{Source: "manual", SourceID: "benchmark", Title: "美国非农就业年度基准初步修订（全国）",
		Kind: "macro", Importance: 3, Start: "2027-08-26T10:00:00-04:00", End: "2027-08-26T10:00:00-04:00",
		Timezone: "America/New_York", Precision: "minute", Status: "confirmed", URL: "https://www.bls.gov/schedule/2027/08_sched_list.htm"}
	old, err := s.Save(e, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := a.Store.DB.Exec(`INSERT INTO calendar_reminder_watches VALUES(?,1)`, old.ID); err != nil {
		t.Fatal(err)
	}
	assertJobs := func(queued, cancelled int) {
		t.Helper()
		reconcileTest(t, a, now)
		var pending, retired int
		err := a.Store.DB.QueryRow(`SELECT count(*) FILTER(WHERE d.status='queued'),count(*) FILTER(WHERE d.status='cancelled')
			FROM calendar_reminder_jobs j JOIN deliveries d ON d.id=j.delivery_id WHERE j.event_id=?`, old.ID).Scan(&pending, &retired)
		if err != nil || pending != queued || retired != cancelled {
			t.Fatal(pending, retired, err)
		}
	}
	assertJobs(2, 0)
	if err := s.ImportBundledSnapshot(); err != nil {
		t.Fatal(err)
	}
	assertJobs(2, 0)
	file := "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:test-national-uid\nSUMMARY:Current Employment Statistics Preliminary Benchmark (National) for March 2027\nDTSTART;TZID=America/New_York:20270827T100000\nEND:VEVENT\nEND:VCALENDAR"
	upload := func(body string) {
		t.Helper()
		const path = "calendar-imports/bls"
		w := perform(a, authed(t, a, "POST", path, body))
		var review calendar.ICSImport
		if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &review) != nil || len(review.Changes) != 1 {
			t.Fatal(w.Code, w.Body.String())
		}
		w = perform(a, authed(t, a, "POST", path+"?confirm="+review.Token, body))
		if w.Code != 200 {
			t.Fatal(w.Code, w.Body.String())
		}
	}
	upload(file)
	assertJobs(2, 2)
	live, err := calendar.Parse("bls", "https://www.bls.gov/schedule/news_release/bls.ics", file, 2027)
	if err != nil {
		t.Fatal(err)
	}
	if err = s.Import("bls", live, now.Unix(), nil); err != nil {
		t.Fatal(err)
	}
	assertJobs(2, 2)
	upload(strings.Replace(file, "END:VEVENT", "STATUS:CANCELLED\nEND:VEVENT", 1))
	assertJobs(0, 4)
	live[0].Status = "cancelled"
	if err = s.Import("bls", live, now.Unix(), nil); err != nil {
		t.Fatal(err)
	}
	assertJobs(0, 4)
}

func TestBLSUploadRequiresSessionOriginAndBoundedFile(t *testing.T) {
	a := testApp(t)
	path := "calendar-imports/bls"
	r := httptest.NewRequest("POST", prefix+path, strings.NewReader("calendar"))
	if w := perform(a, r); w.Code != 401 {
		t.Fatal(w.Code)
	}
	r = authed(t, a, "POST", path, "calendar")
	r.Header.Set("Origin", "https://example.org")
	if w := perform(a, r); w.Code != 403 {
		t.Fatal(w.Code)
	}
	if w := perform(a, authed(t, a, "POST", path, "<html>Forbidden</html>")); w.Code != 400 {
		t.Fatal(w.Code)
	}
	if w := perform(a, authed(t, a, "POST", path, strings.Repeat("x", calendar.MaxICSBytes+1))); w.Code != 413 {
		t.Fatal(w.Code)
	}
}

func TestOfficialSnapshotDownloadRequiresSessionAndDoesNotRenderHTML(t *testing.T) {
	a := testApp(t)
	s := &calendar.Store{DB: a.Store.DB}
	if err := s.ImportBundledSnapshot(); err != nil {
		t.Fatal(err)
	}
	sources, err := s.Sources()
	if err != nil {
		t.Fatal(err)
	}
	var snapshot calendar.SnapshotInfo
	for _, source := range sources {
		if source.ID == "nbs" {
			snapshot = source.Snapshots[0]
		}
	}
	path := "calendar-snapshots/" + snapshot.ID
	if w := perform(a, httptest.NewRequest("GET", prefix+path, nil)); w.Code != 401 {
		t.Fatal(w.Code)
	}
	w := perform(a, authed(t, a, "GET", path, ""))
	if w.Code != 200 || !strings.HasPrefix(w.Header().Get("Content-Type"), "text/plain") || !strings.Contains(w.Header().Get("Content-Disposition"), "attachment") {
		t.Fatal(w.Code, w.Header())
	}
	sum := sha256.Sum256(w.Body.Bytes())
	if hex.EncodeToString(sum[:]) != snapshot.SHA256 {
		t.Fatal("download differs from reviewed original")
	}
	w = perform(a, authed(t, a, "GET", "calendar?from=2026-11-01&to=2026-11-30", ""))
	var result struct {
		Events  []calendar.Event  `json:"events"`
		Sources []calendar.Source `json:"sources"`
	}
	if err = json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	counts := map[string]int{}
	for _, e := range result.Events {
		if e.Provenance == nil || e.Provenance.Mode != "snapshot" {
			t.Fatal(e)
		}
		counts[e.Source]++
	}
	if counts["nbs"] != 4 || counts["bls"] != 10 {
		t.Fatal(counts)
	}
	for _, source := range result.Sources {
		if source.ID == "nbs" && (source.LastSuccess != 0 || source.Snapshots[0].ActiveEntries != 47) {
			t.Fatal(source)
		}
	}
}

func TestCalendarTimeoutDoesNotExtendNewsRequests(t *testing.T) {
	a := NewApp(Config{}, nil)
	if a.Client == a.CalendarClient || a.Client.Timeout != 15*time.Second || a.CalendarClient.Timeout != calendar.RequestTimeout {
		t.Fatal("calendar and news require separate request deadlines")
	}
	for _, c := range []*http.Client{a.Client, a.CalendarClient} {
		if c.CheckRedirect(nil, nil) != http.ErrUseLastResponse {
			t.Fatal("redirect policy changed")
		}
	}
}

func TestCalendarAuthDraftConfirmationAndConflict(t *testing.T) {
	a := testApp(t)
	if w := perform(a, httptest.NewRequest("GET", prefix+"calendar?from=2026-11-01&to=2026-11-30", nil)); w.Code != 401 {
		t.Fatal(w.Code)
	}
	input := `{"text":"基金代码：123456。开放赎回时间为2026年11月2日至2026年11月6日。每日仅限交易时间。","url":"https://example.org/notice"}`
	r := authed(t, a, "POST", "fund-drafts", input)
	r.Header.Set("Origin", "https://evil.test")
	if w := perform(a, r); w.Code != 403 {
		t.Fatal(w.Code)
	}
	w := perform(a, authed(t, a, "POST", "fund-drafts", input))
	if w.Code != 201 {
		t.Fatal(w.Body.String())
	}
	var d calendar.Draft
	if err := json.Unmarshal(w.Body.Bytes(), &d); err != nil {
		t.Fatal(err)
	}
	w = perform(a, authed(t, a, "POST", "fund-drafts/"+d.ID+"/confirm", `{"acknowledged":false}`))
	if w.Code != 400 {
		t.Fatal("confirmation not required")
	}
	b, _ := json.Marshal(map[string]any{"event": d.Event, "acknowledged": true})
	w = perform(a, authed(t, a, "POST", "fund-drafts/"+d.ID+"/confirm", string(b)))
	if w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	var e calendar.Event
	if err := json.Unmarshal(w.Body.Bytes(), &e); err != nil {
		t.Fatal(err)
	}
	e.Start = "2026-11-03"
	b, _ = json.Marshal(e)
	w = perform(a, authed(t, a, "PUT", "calendar/"+e.ID, string(b)))
	if w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	w = perform(a, authed(t, a, "PUT", "calendar/"+e.ID, string(b)))
	if w.Code != 409 {
		t.Fatal("stale edit accepted", w.Body.String())
	}
	w = perform(a, authed(t, a, "GET", "calendar?from=invalid&to=2026-11-30", ""))
	if w.Code != 400 {
		t.Fatal(w.Code)
	}
	store := calendar.Store{DB: a.Store.DB}
	e.Source = "bls"
	e.SourceID = "official"
	e.Kind = "macro"
	official, err := store.Save(e, 0)
	if err != nil {
		t.Fatal(err)
	}
	b, _ = json.Marshal(official)
	w = perform(a, authed(t, a, "PUT", "calendar/"+official.ID, string(b)))
	if w.Code != 403 {
		t.Fatal("official schedule editable", w.Code)
	}
}
