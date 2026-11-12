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
	if len(result.Events) != 4 || result.Events[0].Provenance.Mode != "snapshot" {
		t.Fatal(result.Events)
	}
	for _, source := range result.Sources {
		if source.ID == "nbs" && (source.LastSuccess != 0 || source.Snapshots[0].ActiveEntries != 47) {
			t.Fatal(source)
		}
	}
}

func TestCalendarTimeoutDoesNotExtendNewsRequests(t *testing.T) {
	a := NewApp(Config{}, nil, "")
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
