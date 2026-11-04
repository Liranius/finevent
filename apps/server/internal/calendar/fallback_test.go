package calendar

import (
	"context"
	"errors"
	"io"
	"net/http"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"
)

func TestLiveNYFedSupplement(t *testing.T) {
	if os.Getenv("FINEVENT_LIVE_CALENDAR") != "1" {
		t.Skip("explicit live official-source check only")
	}
	client := NewHTTPClient()
	body, err := Fetch(context.Background(), client, nyfedURL)
	if err != nil {
		t.Fatal(err)
	}
	events, err := fetchNYFed(context.Background(), client, body, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	s := testStore(t)
	if err = s.ImportBundledSnapshot(); err != nil {
		t.Fatal(err)
	}
	if err = s.Import("nyfed", events, time.Now().Unix(), nil); err != nil {
		t.Fatal(err)
	}
	sources, err := s.Sources()
	if err != nil {
		t.Fatal(err)
	}
	for _, source := range sources {
		if source.ID == "nyfed" || source.ID == "nbs" {
			t.Logf("%s: automatic_count=%d last_success=%d coverage=%s snapshots=%+v", source.ID, source.Count, source.LastSuccess, source.CoverageEnd, source.Snapshots)
		}
	}
}

func TestReviewedSnapshotMatchesOfficialFixtureAndSurvivesRestart(t *testing.T) {
	info, body, err := bundledSnapshot()
	if err != nil {
		t.Fatal(err)
	}
	actual, err := Parse("nbs", info.URL, string(body), 2026)
	if err != nil {
		t.Fatal(err)
	}
	expected, err := Parse("nbs", info.URL, fixture(t, "nbs.html"), 2026)
	if err != nil || len(actual) != 47 || len(expected) != 47 {
		t.Fatal(len(actual), len(expected), err)
	}
	for i, e := range actual {
		v := expected[i]
		if e.SourceID != v.SourceID || e.Start != v.Start || e.End != v.End || e.Precision != v.Precision {
			t.Fatal("snapshot differs from reviewed dates", e, v)
		}
	}
	s := testStore(t)
	if err = s.Import("nbs", nil, info.ReviewedAt, errors.New("source timeout")); err != nil {
		t.Fatal(err)
	}
	if err = s.importSnapshot(info, body, info.ReviewedAt+1); err != nil {
		t.Fatal(err)
	}
	if err = Init(s.DB); err != nil {
		t.Fatal(err)
	}
	if err = s.importSnapshot(info, body, info.ReviewedAt+2); err != nil {
		t.Fatal(err)
	}
	sources, err := s.Sources()
	if err != nil {
		t.Fatal(err)
	}
	for _, src := range sources {
		if src.ID == "nbs" && (src.LastSuccess != 0 || src.Error != "source timeout" || len(src.Snapshots) != 1 || src.Snapshots[0].ActiveEntries != 47 || src.Snapshots[0].ImportedAt != info.ReviewedAt+1) {
			t.Fatal(src)
		}
	}
	saved, err := s.Get(Key("nbs:" + actual[0].SourceID))
	if err != nil || saved.Version != 1 || saved.Provenance.Mode != "snapshot" {
		t.Fatal(saved, err)
	}
	raw, err := s.SnapshotBody(info.ID)
	if err != nil || string(raw) != string(body) {
		t.Fatal("original body changed", err)
	}
}

func TestSnapshotNeverOverwritesExistingOrNewerLiveData(t *testing.T) {
	info, body, _ := bundledSnapshot()
	events, _ := Parse("nbs", info.URL, string(body), 2026)
	for _, newer := range []bool{false, true} {
		s := testStore(t)
		live := events[0]
		live.Status = "cancelled"
		at := info.ReviewedAt - 1
		if newer {
			at = info.ReviewedAt + 1
		}
		if err := s.Import("nbs", []Event{live}, at, nil); err != nil {
			t.Fatal(err)
		}
		if err := s.importSnapshot(info, body, info.ReviewedAt+2); err != nil {
			t.Fatal(err)
		}
		got, _ := s.Get(Key("nbs:" + live.SourceID))
		if got.Status != "cancelled" || got.Provenance != nil {
			t.Fatal("overwrote live event", got)
		}
		var count int
		_ = s.DB.QueryRow(`SELECT COUNT(*) FROM calendar_events`).Scan(&count)
		if (newer && count != 1) || (!newer && count != 47) {
			t.Fatal(newer, count)
		}
	}
}

func TestSnapshotChecksumFailureIsAtomicAndLiveImportSupersedesSnapshot(t *testing.T) {
	info, body, _ := bundledSnapshot()
	s := testStore(t)
	if err := s.importSnapshot(info, append(body, 'x'), info.ReviewedAt+1); err == nil {
		t.Fatal("invalid checksum accepted")
	}
	var count int
	_ = s.DB.QueryRow(`SELECT COUNT(*) FROM calendar_events`).Scan(&count)
	if count != 0 {
		t.Fatal(count)
	}
	if err := s.importSnapshot(info, body, info.ReviewedAt+1); err != nil {
		t.Fatal(err)
	}
	events, _ := Parse("nbs", info.URL, string(body), 2026)
	if err := s.Import("nbs", events, info.ReviewedAt+2, nil); err != nil {
		t.Fatal(err)
	}
	if err := s.importSnapshot(info, body, info.ReviewedAt+3); err != nil {
		t.Fatal(err)
	}
	got, _ := s.Get(Key("nbs:" + events[0].SourceID))
	if got.Provenance != nil || got.Version != 2 {
		t.Fatal(got)
	}
	history, _ := s.History(got.ID)
	if history[1].Event.Provenance.Mode != "snapshot" {
		t.Fatal("lost snapshot evidence")
	}
	infos, err := s.snapshotInfo("nbs")
	if err != nil || infos[0].ActiveEntries != 0 {
		t.Fatal(infos, err)
	}
}

func TestNYFedOfficialMonthsAndDST(t *testing.T) {
	for _, tc := range []struct {
		file  string
		count int
		start string
	}{{"nyfed-oct2026.html", 5, "2026-10-02T08:30:00-04:00"}, {"nyfed-nov2026.html", 6, "2026-11-03T10:00:00-05:00"}} {
		events, err := Parse("nyfed", nyfedURL, fixture(t, tc.file), 2026)
		if err != nil || len(events) != tc.count || events[0].Start != tc.start {
			t.Fatal(tc, events, err)
		}
		for _, e := range events {
			if e.Provenance.Mode != "supplement" || !strings.HasPrefix(e.Provenance.AuthorityURL, "https://www.bls.gov/") {
				t.Fatal(e)
			}
		}
	}
}

func TestNYFedRescheduleAndUnsupportedContentFailClosed(t *testing.T) {
	body := fixture(t, "nyfed-nov2026.html")
	events, err := Parse("nyfed", nyfedURL, body, 2026)
	if err != nil {
		t.Fatal(err)
	}
	s := testStore(t)
	if err = s.Import("nyfed", events, 100, nil); err != nil {
		t.Fatal(err)
	}
	changed := regexp.MustCompile(`(<div>\s*)03`).ReplaceAllString(body, "${1}04")
	revised, err := Parse("nyfed", nyfedURL, changed, 2026)
	if err != nil {
		t.Fatal(err)
	}
	if err = s.Import("nyfed", revised, 200, nil); err != nil {
		t.Fatal(err)
	}
	got, _ := s.Get(Key("nyfed:" + events[0].SourceID))
	if got.Status != "rescheduled" || got.Version != 2 {
		t.Fatal(got)
	}
	for _, bad := range []string{regexp.MustCompile(`(?s)(>JOLTS</a\s*>\s*<br\s*/?>\s*)\(10:00\)`).ReplaceAllString(body, "${1}unknown time"), strings.Replace(body, ">JOLTS<", ">Unknown Release<", 1)} {
		if bad == body {
			t.Fatal("fixture mutation did not reach the BLS event")
		}
		if _, err = Parse("nyfed", nyfedURL, bad, 2026); err == nil {
			t.Fatal("unknown source format accepted")
		}
	}
	if _, _, err = nyfedNext(strings.Replace(body, "/research/calendars/i-dec26.html", "https://evil.test/", 1), nyfedURL); err == nil {
		t.Fatal("unsafe next page accepted")
	}
}

func TestNYFedNextMonthFetchAndFailurePreservesAtomicity(t *testing.T) {
	body := fixture(t, "nyfed-oct2026.html")
	now := time.Date(2026, 11, 1, 1, 0, 0, 0, time.UTC) // Still October in New York.
	calls := 0
	client := NewHTTPClient()
	client.Transport = transport(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.URL.String() != "https://www.newyorkfed.org/research/calendars/i-nov26.html" {
			t.Fatal(r.URL)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(fixture(t, "nyfed-nov2026.html")))}, nil
	})
	events, err := fetchNYFed(context.Background(), client, body, now)
	if err != nil || len(events) != 11 || calls != 1 {
		t.Fatal(len(events), calls, err)
	}
	client.Transport = transport(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 503, Body: io.NopCloser(strings.NewReader(""))}, nil
	})
	if events, err = fetchNYFed(context.Background(), client, body, now); err == nil || events != nil {
		t.Fatal("partial fetch treated as complete")
	}
	if _, err = fetchNYFed(context.Background(), client, body, now.AddDate(0, 1, 0)); err == nil {
		t.Fatal("stale current month accepted")
	}
	dec := strings.ReplaceAll(fixture(t, "nyfed-nov2026.html"), "November 2026", "December 2026")
	dec = strings.Replace(dec, "i-dec26.html", "i-jan27.html", 1)
	link, _, err := nyfedNext(dec, nyfedURL)
	if err != nil || !strings.HasSuffix(link, "i-jan27.html") {
		t.Fatal(link, err)
	}
}

func TestNYFedEarlyMonthRolloverFollowsPublishedPreviousMonth(t *testing.T) {
	// The official landing page showed November on October 31, before New York's month boundary.
	body := fixture(t, "nyfed-nov2026.html")
	for _, now := range []time.Time{
		time.Date(2026, 10, 31, 8, 9, 11, 0, time.UTC),
		time.Date(2026, 11, 1, 1, 0, 0, 0, time.UTC), // Still October in New York.
	} {
		calls := 0
		client := NewHTTPClient()
		client.Transport = transport(func(r *http.Request) (*http.Response, error) {
			calls++
			if r.URL.String() != "https://www.newyorkfed.org/research/calendars/i-oct26.html" {
				t.Fatalf("must follow the published October link, not fetch December: %s", r.URL)
			}
			return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(fixture(t, "nyfed-oct2026.html")))}, nil
		})
		events, err := fetchNYFed(context.Background(), client, body, now)
		if err != nil || len(events) != 11 || calls != 1 {
			t.Fatalf("early month rollover: events=%d calls=%d error=%v", len(events), calls, err)
		}
		if events[0].Start != "2026-10-02T08:30:00-04:00" || events[5].Start != "2026-11-03T10:00:00-05:00" {
			t.Fatal("actual source month or DST lost", events)
		}
		if events[0].URL != "https://www.newyorkfed.org/research/calendars/i-oct26.html" || events[5].URL != nyfedURL {
			t.Fatal("source URLs do not identify the fetched pages")
		}
	}
}

func TestNYFedEarlyRolloverRequiresVerifiedCurrentMonth(t *testing.T) {
	body := fixture(t, "nyfed-nov2026.html")
	previous := "/research/calendars/i-oct26.html"
	now := time.Date(2026, 10, 31, 8, 0, 0, 0, time.UTC)
	for _, tc := range []struct {
		name, landing, response string
		status, calls           int
	}{
		{"missing previous", strings.Replace(body, "PREVIOUS MONTH", "ARCHIVE", 1), "", 200, 0},
		{"foreign host", strings.Replace(body, previous, "https://evil.test"+previous, 1), "", 200, 0},
		{"wrong linked month", strings.Replace(body, previous, "/research/calendars/i-sep26.html", 1), "", 200, 0},
		{"previous unavailable", body, "", 503, 1},
		{"wrong returned month", body, body, 200, 1},
		{"invalid returned body", body, "<html>unavailable</html>", 200, 1},
		{"too far ahead", strings.Replace(body, "November 2026", "December 2026", 1), "", 200, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			client := NewHTTPClient()
			client.Transport = transport(func(r *http.Request) (*http.Response, error) {
				calls++
				return &http.Response{StatusCode: tc.status, Body: io.NopCloser(strings.NewReader(tc.response))}, nil
			})
			events, err := fetchNYFed(context.Background(), client, tc.landing, now)
			if err == nil || events != nil || calls != tc.calls {
				t.Fatal("unverified or partial month imported", events, calls, err)
			}
			s := testStore(t)
			existing, parseErr := Parse("nyfed", nyfedURL, fixture(t, "nyfed-oct2026.html"), 2026)
			if parseErr != nil {
				t.Fatal(parseErr)
			}
			if importErr := s.Import("nyfed", existing, 100, nil); importErr != nil {
				t.Fatal(importErr)
			}
			if importErr := s.Import("nyfed", events, 200, err); importErr != nil {
				t.Fatal(importErr)
			}
			sources, sourceErr := s.Sources()
			if sourceErr != nil {
				t.Fatal(sourceErr)
			}
			for _, source := range sources {
				if source.ID == "nyfed" && (source.Count != 5 || source.LastSuccess != 100 || source.Error == "") {
					t.Fatal("prior data or success evidence lost", source)
				}
			}
			stored, getErr := s.Get(Key("nyfed:" + existing[0].SourceID))
			if getErr != nil || stored.Version != 1 || stored.Start != existing[0].Start {
				t.Fatal("existing event changed after failed import", stored, getErr)
			}
		})
	}
}

func TestNYFedEarlyRolloverAcrossYear(t *testing.T) {
	// Shift the fixture months only to exercise December/January routing, not to assert real future dates.
	landing := strings.NewReplacer("November 2026", "January 2027", "i-oct26.html", "i-dec26.html", "i-dec26.html", "i-feb27.html").Replace(fixture(t, "nyfed-nov2026.html"))
	previous := strings.NewReplacer("October 2026", "December 2026", "i-nov26.html", "i-jan27.html").Replace(fixture(t, "nyfed-oct2026.html"))
	client := NewHTTPClient()
	client.Transport = transport(func(r *http.Request) (*http.Response, error) {
		if r.URL.Path != "/research/calendars/i-dec26.html" {
			t.Fatal(r.URL)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(previous))}, nil
	})
	events, err := fetchNYFed(context.Background(), client, landing, time.Date(2027, 1, 1, 1, 0, 0, 0, time.UTC))
	if err != nil || len(events) != 11 || !strings.HasPrefix(events[0].Start, "2026-12-") || !strings.HasPrefix(events[5].Start, "2027-01-") {
		t.Fatal(events, err)
	}
}
