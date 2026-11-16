package calendar

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"strings"
	"testing"
)

func TestPackageBLSAnnualTablesPreserveFailureAndReleaseScope(t *testing.T) {
	s := testStore(t)
	const now = 1800000000
	if err := s.Import("bls", nil, now, errors.New("HTTP 403")); err != nil {
		t.Fatal(err)
	}
	for range 2 {
		if err := s.importBLSTestPackage(t, now); err != nil {
			t.Fatal(err)
		}
	}
	sources, err := s.Sources()
	if err != nil {
		t.Fatal(err)
	}
	for _, src := range sources {
		if src.ID == "bls" && (src.Error != "HTTP 403" || src.Count != 0 || src.LastSuccess != 0 || len(src.Snapshots) != 3) {
			t.Fatal(src)
		}
	}
	var count, revisions int
	if err := s.DB.QueryRow(`SELECT count(*) FROM calendar_events`).Scan(&count); err != nil || count != 11 {
		t.Fatal(count, err)
	}
	if err := s.DB.QueryRow(`SELECT count(*) FROM calendar_revisions`).Scan(&revisions); err != nil || revisions != 12 {
		t.Fatal(revisions, err)
	}
	day, err := s.List("2027-08-26", "2027-08-26")
	if err != nil || len(day) != 3 {
		t.Fatal(day, err)
	}
	seen := map[string]bool{}
	for _, e := range day {
		if e.Provenance.Format != "release-table" || e.Start != "2027-08-26T10:00:00-04:00" {
			t.Fatal(e)
		}
		seen[e.Title] = true
	}
	if !seen["美国非农就业年度基准初步修订（全国）"] || !seen["美国就业年度基准初步修订（州及地区）"] {
		t.Fatal(seen)
	}
	day, err = s.List("2027-02-05", "2027-02-05")
	if err != nil || len(day) != 1 || !strings.Contains(day[0].Conditions, "最终修订") || day[0].Provenance.AuthorityURL != dolBenchmarkNotice {
		t.Fatal(day, err)
	}
	day, err = s.List("2026-12-04", "2026-12-04")
	if err != nil || len(day) != 1 || day[0].Start != "2026-12-04T09:30:00-05:00" || !strings.HasPrefix(day[0].URL, "https://www.dol.gov/") {
		t.Fatal(day, err)
	}
}

func TestBLSSnapshotKeepsNYFedIdentityAcrossSourcesAndCancellation(t *testing.T) {
	for _, first := range []string{"nyfed", "snapshot", "bls"} {
		t.Run(first, func(t *testing.T) {
			s := testStore(t)
			events, err := Parse("nyfed", nyfedURL, fixture(t, "nyfed-nov2026.html"), 2026)
			if err != nil {
				t.Fatal(err)
			}
			var fed Event
			for _, row := range events {
				if row.SourceID == "2026-11:empsit" {
					fed = row
				}
			}
			live := event("bls", "official-uid", "Employment Situation", "https://www.bls.gov/schedule/news_release/bls.ics", easternZone)
			live.Start, live.End, live.Precision = fed.Start, fed.End, "minute"
			live.Evidence = "The Employment Situation for October 2026 is scheduled to be published in November."
			if first == "nyfed" {
				if err := s.Import("nyfed", []Event{fed}, 100, nil); err != nil {
					t.Fatal(err)
				}
			} else if first == "bls" {
				if err := s.Import("bls", []Event{live}, 100, nil); err != nil {
					t.Fatal(err)
				}
			}
			if err := s.importBLSTestPackage(t, 1800000000); err != nil {
				t.Fatal(err)
			}
			var id string
			if err := s.DB.QueryRow(`SELECT event_id FROM calendar_bls_releases WHERE name='Employment Situation' AND period='October 2026'`).Scan(&id); err != nil {
				t.Fatal(err)
			}
			if first == "nyfed" && id != Key("nyfed:"+fed.SourceID) || first == "bls" && id != Key("bls:"+live.SourceID) {
				t.Fatal("lost existing event ID", id)
			}
			fed.Start, fed.End = "2026-11-09T08:30:00-05:00", "2026-11-09T08:30:00-05:00"
			if err := s.Import("nyfed", []Event{fed}, 1800000000, nil); err != nil {
				t.Fatal(err)
			}
			fromFed, err := s.Get(id)
			if err != nil || (first != "bls" && (fromFed.Start != fed.Start || fromFed.Source != "nyfed")) {
				t.Fatal(fromFed, err)
			}
			for _, batch := range []Event{fed, live} {
				if err := s.Import(batch.Source, []Event{batch}, 1800000001, nil); err != nil {
					t.Fatal(err)
				}
			}
			live.Start, live.End = "2026-12-01T09:00:00-05:00", "2026-12-01T09:00:00-05:00"
			live.Title = "Employment Situation"
			if err := s.Import("bls", []Event{live}, 1800000002, nil); err != nil {
				t.Fatal(err)
			}
			got, err := s.Get(id)
			if err != nil || got.Start != live.Start || got.Status != "rescheduled" || got.Provenance != nil {
				t.Fatal(got, err)
			}
			live.Status = "cancelled"
			if err := s.Import("bls", []Event{live}, 1800000003, nil); err != nil {
				t.Fatal(err)
			}
			if err := s.Import("nyfed", []Event{fed}, 1800000004, nil); err != nil {
				t.Fatal(err)
			}
			if err := s.importBLSTestPackage(t, 1800000005); err != nil {
				t.Fatal(err)
			}
			got, err = s.Get(id)
			if err != nil || got.Status != "cancelled" {
				t.Fatal(got, err)
			}
			var n int
			if err := s.DB.QueryRow(`SELECT count(*) FROM calendar_events`).Scan(&n); err != nil || n != 11 {
				t.Fatal(n, err)
			}
		})
	}
}

func TestReviewedDOLRescheduleRetainsBenchmarkAndRejectsBadBatch(t *testing.T) {
	s := testStore(t)
	const now = 1800000000
	manual := event("manual", "followed-benchmark", "美国非农就业年度基准初步修订（全国）", "https://www.bls.gov/schedule/2027/08_sched_list.htm", easternZone)
	manual.Start, manual.End, manual.Precision = "2027-08-26T10:00:00-04:00", "2027-08-26T10:00:00-04:00", "minute"
	old, err := s.Save(manual, 0)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.importBLSTestPackage(t, now); err != nil {
		t.Fatal(err)
	}
	body := []byte("date_et\ttime_et\trelease\treference_period\n2027-09-01\t10:00\t" + nationalBenchmark + "\tMarch 2027\n")
	digest := fmt.Sprintf("%x", sha256.Sum256(body))
	info := SnapshotInfo{Format: "release-table", ID: "bls-notice-08062027-" + digest[:12], SHA256: digest, URL: "https://www.dol.gov/newsroom/economicdata/empsit_08062027.pdf", Year: 2027, Entries: 1, RetrievedAt: now, ReviewedAt: now}
	tx, err := s.DB.Begin()
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	if err := importBLSTable(tx, info, body, now); err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	got, err := s.Get(old.ID)
	if err != nil || got.Start != "2027-09-01T10:00:00-04:00" || got.Status != "rescheduled" || got.URL != info.URL {
		t.Fatal(got, err)
	}
	if _, err := readBLSReleases(info, append(body, 'x'), now); err == nil {
		t.Fatal("tampered schedule accepted")
	}
	info.URL = "https://example.org/schedule"
	if _, err := readBLSReleases(info, body, now); err == nil {
		t.Fatal("unverified authority accepted")
	}
}

func TestNYFedMonthWithoutReferencePeriodCannotMergeDistinctReleases(t *testing.T) {
	s := testStore(t)
	const now = 1800000000
	if err := s.importBLSTestPackage(t, now); err != nil {
		t.Fatal(err)
	}
	for _, day := range []string{"14", "30"} {
		fed := event("nyfed", "2026-01:ppi", "美国生产者价格指数", nyfedURL, easternZone)
		fed.Start, fed.Precision = "2026-01-"+day+"T08:30:00-05:00", "minute"
		fed.End = fed.Start
		if err := s.Import("nyfed", []Event{fed}, now, nil); err != nil {
			t.Fatal(err)
		}
	}
	var count int
	if err := s.DB.QueryRow(`SELECT count(*) FROM calendar_events e JOIN calendar_bls_releases r ON e.id=r.event_id
		WHERE r.nyfed_id='2026-01:ppi' AND e.source='bls'`).Scan(&count); err != nil || count != 2 {
		t.Fatal(count, err)
	}
}

func TestStaleNYFedMonthAfterOfficialReschedule(t *testing.T) {
	s := testStore(t)
	const now = 1800000000
	if err := s.importBLSTestPackage(t, now); err != nil {
		t.Fatal(err)
	}
	body := []byte("date_et\ttime_et\trelease\treference_period\n2026-12-02\t08:30\tEmployment Situation\tOctober 2026\n")
	digest := fmt.Sprintf("%x", sha256.Sum256(body))
	info := SnapshotInfo{Format: "release-table", ID: "bls-notice-11012026-" + digest[:12], SHA256: digest, URL: "https://www.dol.gov/newsroom/economicdata/empsit_11012026.pdf", Year: 2026, Entries: 1, RetrievedAt: now, ReviewedAt: now}
	tx, err := s.DB.Begin()
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	if err := importBLSTable(tx, info, body, now); err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	stale := event("nyfed", "2026-11:empsit", "美国就业形势", nyfedURL, easternZone)
	stale.Start, stale.Precision = "2026-11-06T08:30:00-05:00", "minute"
	stale.End = stale.Start
	if err := s.Import("nyfed", []Event{stale}, now, nil); err != nil {
		t.Fatal(err)
	}
	var total int
	if err := s.DB.QueryRow(`SELECT count(*) FROM calendar_events`).Scan(&total); err != nil || total != 11 {
		t.Fatal(total, err)
	}
	day, err := s.List("2026-12-02", "2026-12-02")
	found := false
	for _, item := range day {
		if item.URL == info.URL && item.Status == "rescheduled" {
			found = true
		}
	}
	if err != nil || !found {
		t.Fatal(day, err)
	}
}
