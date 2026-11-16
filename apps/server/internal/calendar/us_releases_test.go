package calendar

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"

	"golang.org/x/net/html"
)

const retailSample = `<h2><strong>Advance Monthly Retail Trade Report</strong></h2>
<table><tr><td>Data Month</td><td>Release Date at 8:30 am</td></tr>
<tr><td>September 2026</td><td>October 15, 2026</td></tr>
<tr><td>October 2026</td><td>November 17, 2026</td></tr>
<tr><td>December 2027</td><td>To be announced at a later date</td></tr></table>
<h2>Monthly Retail Trade Report</h2><table><tr><td>August 2026</td><td>October 15, 2026</td></tr></table>`

const claimsSample = `<p>Publication Schedule: The UI Weekly Claims News Release is published each week on Thursday morning at 8:30am EST. Exceptions to this schedule occur when a Thursday falls on a Federal Holiday.</p>
<table><thead><tr><th>Release Date</th><th>Release Time</th></tr></thead>
<tbody><tr><td>Wednesday, November 25, 2026</td><td>8:30 AM EST</td></tr></tbody></table>`

func TestRetailSalesUsesReferenceMonthAndOnlyAdvanceReport(t *testing.T) {
	events, err := Parse("census", censusRetailURL, retailSample, 2026)
	if err != nil || len(events) != 2 || events[0].Start != "2026-10-15T08:30:00-04:00" || events[1].Start != "2026-11-17T08:30:00-05:00" {
		t.Fatal(events, err)
	}
	if events[1].SourceID != "advance-retail:2026-10" || events[1].URL != censusRetailURL {
		t.Fatal(events[1])
	}
	s := testStore(t)
	if err := s.Import("census", events, 100, nil); err != nil {
		t.Fatal(err)
	}
	revised, err := Parse("census", censusRetailURL, strings.Replace(retailSample, "November 17", "December 1", 1), 2026)
	if err != nil {
		t.Fatal(err)
	}
	for _, at := range []int64{200, 300} {
		if err := s.Import("census", revised, at, nil); err != nil {
			t.Fatal(err)
		}
	}
	e, err := s.Get(Key("census:" + events[1].SourceID))
	if err != nil || e.Version != 2 || e.Status != "rescheduled" || e.Start != "2026-12-01T08:30:00-05:00" {
		t.Fatal(e, err)
	}
	for _, tc := range []struct{ from, to string }{
		{"Release Date at 8:30 am", "Release Date at noon"}, {"November 17, 2026", "November 31, 2026"},
	} {
		if events, err := Parse("census", censusRetailURL, strings.Replace(retailSample, tc.from, tc.to, 1), 2026); err == nil || events != nil {
			t.Fatal("accepted partial retail schedule", tc)
		}
	}
}

func TestInitialClaimsUsesOfficialExceptionsAndLocalDates(t *testing.T) {
	// Still October 28 in New York. Include October 29 and stop before December.
	now := time.Date(2026, 10, 29, 1, 0, 0, 0, time.UTC)
	root, _ := html.Parse(strings.NewReader(claimsSample))
	events, err := parseInitialClaims(root, dolClaimsURL, now)
	want := []string{"2026-10-29T08:30:00-04:00", "2026-11-05T08:30:00-05:00", "2026-11-12T08:30:00-05:00", "2026-11-19T08:30:00-05:00", "2026-11-25T08:30:00-05:00"}
	if err != nil || len(events) != len(want) {
		t.Fatal(events, err)
	}
	for i, e := range events {
		if e.Start != want[i] || e.End != e.Start || e.Validate() != nil || e.URL != dolClaimsURL {
			t.Fatal(e)
		}
	}
	holiday := events[len(events)-1]
	if holiday.SourceID != "initial-claims:2026-11-26" || !strings.Contains(holiday.Evidence, "例外") {
		t.Fatal(holiday)
	}
	// A change to the holiday date must update the same followed event exactly once.
	root, _ = html.Parse(strings.NewReader(strings.Replace(claimsSample, "Wednesday, November 25", "Friday, November 27", 1)))
	revised, err := parseInitialClaims(root, dolClaimsURL, now)
	if err != nil {
		t.Fatal(err)
	}
	s := testStore(t)
	for i, batch := range [][]Event{events, revised, revised} {
		if err := s.Import("dol", batch, int64(i+1), nil); err != nil {
			t.Fatal(err)
		}
	}
	got, err := s.Get(Key("dol:" + holiday.SourceID))
	if err != nil || got.Version != 2 || got.Status != "rescheduled" || got.Start != "2026-11-27T08:30:00-05:00" {
		t.Fatal(got, err)
	}
}

func TestClaimsRejectsChangedPublicationRuleOrInvalidException(t *testing.T) {
	for _, tc := range []struct{ from, to string }{
		{"Thursday morning", "Friday morning"}, {"Release Time", "unavailable"},
		{"Wednesday, November 25", "Tuesday, November 25"}, {"8:30 AM EST", "unknown"},
		{"</tbody>", "<tr><td>Friday, November 27, 2026</td><td>8:30 AM EST</td></tr></tbody>"},
	} {
		events, err := Parse("dol", dolClaimsURL, strings.Replace(claimsSample, tc.from, tc.to, 1), 2026)
		if err == nil || events != nil {
			t.Fatal("accepted invalid publication schedule", tc)
		}
	}
}

func TestLiveRetailAndClaimsSync(t *testing.T) {
	if os.Getenv("FINEVENT_LIVE_CALENDAR") != "1" {
		t.Skip("explicit live official-source check only")
	}
	s := testStore(t)
	now := time.Now()
	for _, source := range Sources() {
		if source.ID != "census" && source.ID != "dol" {
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
	for _, id := range []string{"census", "dol"} {
		found := false
		for _, src := range sources {
			if src.ID == id {
				found = src.Count > 0 && src.LastSuccess > 0 && src.Error == ""
				t.Logf("%s: count=%d through=%s error=%q", id, src.Count, src.CoverageEnd, src.Error)
			}
		}
		if !found {
			t.Errorf("%s did not sync", id)
		}
	}
	list, err := s.List(now.Format(time.DateOnly), now.AddDate(0, 2, 0).Format(time.DateOnly))
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range list {
		t.Logf("%s %s", e.Title, e.Start)
	}
}
