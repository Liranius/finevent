package calendar

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"
)

// Minimal generated PDF: exercise the reader, row geometry and year validation
// without shipping the official schedule in the repository.
func sampleOMB(year int, days string, split bool) string {
	var stream strings.Builder
	line := func(y int, text string) {
		fmt.Fprintf(&stream, "BT /F1 10 Tf 1 0 0 1 10 %d Tm (%s) Tj ET\n", y, text)
	}
	line(600, fmt.Sprintf("PRINCIPAL FEDERAL ECONOMIC INDICATORS FOR %d", year))
	line(580, "DEPT AGENCY/INDICATORS JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC")
	label := "Advance Monthly Sales for Retail and Food Services"
	if split {
		line(561, days)
		line(560, label)
	} else {
		line(560, label+" "+days)
	}
	line(540, `\(Data are for previous month\)`)
	objects := []string{
		"<< /Type /Catalog /Pages 2 0 R >>",
		"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
		"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 900 700] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
		"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
		fmt.Sprintf("<< /Length %d >>\nstream\n%sendstream", stream.Len(), stream.String()),
	}
	var body strings.Builder
	body.WriteString("%PDF-1.4\n")
	offsets := []int{}
	for i, obj := range objects {
		offsets = append(offsets, body.Len())
		fmt.Fprintf(&body, "%d 0 obj\n%s\nendobj\n", i+1, obj)
	}
	xref := body.Len()
	fmt.Fprintf(&body, "xref\n0 %d\n0000000000 65535 f \n", len(objects)+1)
	for _, offset := range offsets {
		fmt.Fprintf(&body, "%010d 00000 n \n", offset)
	}
	fmt.Fprintf(&body, "trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n", xref)
	return body.String()
}

const sampleDays = "15 17 16 16 14 16 16 16 16 15 16 15"

func TestOMBAnnualSchedule(t *testing.T) {
	for _, split := range []bool{false, true} {
		doc := sampleOMB(2027, sampleDays, split)
		events, err := parseOMB(doc, publishedOMB[2027], 2027)
		if err != nil || len(events) != 12 || events[0].Start != "2027-01-15" || events[0].SourceID != retailPrefix+"2026-12" ||
			events[11].End != "2027-12-15" || events[0].Precision != "date" {
			t.Fatal(events, err)
		}
	}
	for _, body := range []string{"<html>blocked</html>", sampleOMB(2026, sampleDays, false),
		sampleOMB(2027, "15 31 16 16 14 16 16 16 16 15 16 15", true), sampleOMB(2027, "15 17", false)} {
		if events, err := parseOMB(body, publishedOMB[2027], 2027); err == nil || events != nil {
			t.Fatal("accepted an invalid annual schedule")
		}
	}
}

func TestOMBDiscoversPublishedYearsAndKeepsFailedBatchAtomic(t *testing.T) {
	link := func(year int) string {
		return fmt.Sprintf(`<a href="/%d.pdf">Schedule of Release Dates for Principal Federal Economic Indicators for %d</a>`, year, year)
	}
	index := link(2028) + link(2029)
	bodies := map[string]string{"/2028.pdf": sampleOMB(2028, sampleDays, true), "/2029.pdf": sampleOMB(2029, sampleDays, false)}
	client := NewHTTPClient()
	client.Transport = transport(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(bodies[r.URL.Path])), Header: make(http.Header)}, nil
	})
	events, err := fetchOMB(context.Background(), client, index, nil, 2028)
	if err != nil || len(events) != 24 || events[23].URL != "https://www.whitehouse.gov/2029.pdf" {
		t.Fatal(events, err)
	}
	delete(bodies, "/2029.pdf")
	if events, err = fetchOMB(context.Background(), client, index, nil, 2028); err == nil || events != nil {
		t.Fatal("accepted a partial two-year batch")
	}
	for _, body := range []string{link(2029), strings.Replace(index, "/2028.pdf", "https://example.org/calendar.pdf", 1)} {
		if _, err := ombLinks(body, 2028); err == nil {
			t.Fatal("accepted wrong year or offsite index link")
		}
	}
}

func TestRetailSourcesShareHistoryAndKeepPrecision(t *testing.T) {
	s := testStore(t)
	annual, err := parseOMB(sampleOMB(2026, sampleDays, false), publishedOMB[2026], 2026)
	if err != nil {
		t.Fatal(err)
	}
	nov := annual[10]
	ny, err := Parse("nyfed", nyfedURL, fixture(t, "nyfed-nov2026.html"), 2026)
	if err != nil {
		t.Fatal(err)
	}
	census, err := Parse("census", censusRetailURL, retailSample, 2026)
	if err != nil {
		t.Fatal(err)
	}
	put := func(source string, events []Event, at int64) {
		t.Helper()
		if err := s.Import(source, events, at, nil); err != nil {
			t.Fatal(err)
		}
	}
	id := Key("census:" + retailPrefix + "2026-10")
	check := func(source, start string, version int) {
		t.Helper()
		event, err := s.Get(id)
		if err != nil || event.Source != source || event.Start != start || event.Version != version {
			t.Fatal(event, err)
		}
	}
	// Coincident date upgraded to a known time is not a rescheduling.
	nov.Start, nov.End = "2026-11-17", "2026-11-17"
	put("omb", []Event{nov}, 100)
	put("nyfed", ny, 101)
	check("nyfed", "2026-11-17T08:30:00-05:00", 2)
	e, _ := s.Get(id)
	if e.Status != "scheduled" {
		t.Fatal(e.Status)
	}
	put("omb", []Event{nov}, 102)
	put("census", census, 103)
	put("nyfed", ny, 104)
	put("omb", []Event{nov}, 105)
	put("census", census, 106)
	check("census", "2026-11-17T08:30:00-05:00", 3)
	census[1].Start, census[1].End = "2026-12-01T08:30:00-05:00", "2026-12-01T08:30:00-05:00"
	put("census", census, 107)
	if err := s.Import("census", nil, 108, errors.New("官方来源返回 HTTP 403")); err != nil {
		t.Fatal(err)
	}
	put("nyfed", ny, 109) // The monthly page may still show the old date.
	check("census", census[1].Start, 4)
	retail := e
	retail.SourceID, retail.Start, retail.End = retailPrefix+"2026-12-01", census[1].Start, census[1].End
	put("nyfed", []Event{retail}, 109)
	check("census", census[1].Start, 4)
	if _, err := s.Get(Key("census:" + retailPrefix + "2026-11")); err == nil {
		t.Fatal("duplicated the cross-month release")
	}
	history, err := s.History(id)
	if err != nil || len(history) != 4 || history[0].Event.Status != "rescheduled" {
		t.Fatal(history, err)
	}
}

func TestAmbiguousNYFedRetailDatesDoNotInventPeriods(t *testing.T) {
	s := testStore(t)
	rows := []Event{}
	for _, day := range []string{"2026-11-03", "2026-11-17"} {
		e := event("nyfed", retailPrefix+day, retailTitle, nyfedURL, easternZone)
		e.Start, e.End, e.Precision = day+"T08:30:00-05:00", day+"T08:30:00-05:00", "minute"
		rows = append(rows, e)
	}
	if err := s.Import("nyfed", rows, 100, nil); err != nil {
		t.Fatal(err)
	}
	list, err := s.List("2026-11-01", "2026-11-30")
	if err != nil || len(list) != 0 {
		t.Fatal(list, err)
	}
	var matched int
	if err := s.DB.QueryRow("SELECT count FROM calendar_sources WHERE id='nyfed'").Scan(&matched); err != nil || matched != 0 {
		t.Fatal("reported ambiguous rows as coverage", matched, err)
	}
	legacy := rows[0]
	legacy.Source, legacy.SourceID, legacy.URL = "census", retailPrefix+"2026-09", censusRetailURL
	if _, err := s.Save(legacy, 0); err != nil {
		t.Fatal(err)
	}
	if err := s.Import("nyfed", rows, 200, nil); err != nil {
		t.Fatal(err)
	}
	var matchedPeriod string
	if err := s.DB.QueryRow("SELECT period FROM calendar_retail_schedule WHERE source='nyfed'").Scan(&matchedPeriod); err != nil || matchedPeriod != "2026-09" {
		t.Fatal("did not match pre-upgrade publisher evidence", matchedPeriod, err)
	}
}

func TestLiveRetailSupplementsDuringCensus403(t *testing.T) {
	if os.Getenv("FINEVENT_LIVE_CALENDAR") != "1" {
		t.Skip("explicit live official-source check only")
	}
	s := testStore(t)
	now := time.Now()
	for _, source := range Sources() {
		if source.ID == "census" {
			if err := s.Import(source.ID, nil, now.Unix(), errors.New("官方来源返回 HTTP 403")); err != nil {
				t.Fatal(err)
			}
		} else if source.ID != "nyfed" && source.ID != "omb" {
			if err := s.Import(source.ID, nil, now.Unix(), nil); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := s.SyncDue(context.Background(), NewHTTPClient()); err != nil {
		t.Fatal(err)
	}
	sources, err := s.Sources()
	if err != nil {
		t.Fatal(err)
	}
	for _, src := range sources {
		if src.ID == "nyfed" || src.ID == "omb" {
			if src.Count == 0 || src.Error != "" {
				t.Fatalf("%s: %s", src.ID, src.Error)
			}
			t.Log(src.ID, src.Count, src.CoverageEnd)
		}
	}
	var stored, periods int
	if err := s.DB.QueryRow(`SELECT (SELECT count(*) FROM calendar_events WHERE source_id LIKE 'advance-retail:%'),
		(SELECT count(DISTINCT period) FROM calendar_retail_schedule)`).Scan(&stored, &periods); err != nil || stored != periods {
		t.Fatal("retail periods and visible events differ", stored, periods, err)
	}
	rows, err := s.DB.Query("SELECT source_id,json_extract(payload,'$.start') FROM calendar_events WHERE source='nyfed' AND source_id LIKE 'advance-retail:%' ORDER BY start_day")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var recent int
	for rows.Next() {
		var period, start string
		if err := rows.Scan(&period, &start); err != nil {
			t.Fatal(err)
		}
		t.Log(period, start)
		recent++
	}
	if err := rows.Err(); err != nil || recent == 0 {
		t.Fatal(recent, err)
	}
	t.Log("distinct retail releases", stored)
}
