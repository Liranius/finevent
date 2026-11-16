package calendar

import (
	"errors"
	"fmt"
	"strings"
	"testing"
)

func blsReleaseICS(uid, title, start string) string {
	return fmt.Sprintf("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:%s\r\nSUMMARY:%s\r\nDTSTART:%s\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n", uid, title, start)
}

func TestBLSFileAndAnnualTableKeepSameReleaseIDs(t *testing.T) {
	const now = 1800000000
	rows := []struct{ uid, summary, start, name, period string }{
		{"preliminary", "Productivity and Costs", "20260108T133000Z", "Productivity and Costs (P)", "Third Quarter 2025"},
		{"revised", "Productivity and Costs", "20260129T133000Z", "Productivity and Costs (R)", "Third Quarter 2025"},
		{"requirements", "Occupational Requirements in the United States  ", "20260116T150000Z", "Occupational Requirements in the United States", "Annual 2025"},
	}
	for _, first := range []string{"annual", "ics"} {
		t.Run(first, func(t *testing.T) {
			s := testStore(t)
			if first == "annual" {
				if err := s.importBLSTestPackage(t, now); err != nil {
					t.Fatal(err)
				}
			}
			for _, row := range rows {
				file := []byte(blsReleaseICS(row.uid, row.summary, row.start))
				preview, err := s.ImportBLSFile(file, "", now)
				if err != nil || len(preview.Changes) != 1 {
					t.Fatal(row.uid, preview, err)
				}
				wantAction := "add"
				if first == "annual" {
					wantAction = "update"
				}
				if preview.Changes[0].Action != wantAction {
					t.Errorf("%s: got %s, want %s", row.uid, preview.Changes[0].Action, wantAction)
				}
				if _, err := s.ImportBLSFile(file, preview.Token, now); err != nil {
					t.Fatal(row.uid, err)
				}
			}
			if first == "ics" {
				if err := s.importBLSTestPackage(t, now); err != nil {
					t.Fatal(err)
				}
			}
			for _, row := range rows {
				var id, uid string
				err := s.DB.QueryRow(`SELECT e.id,e.source_id FROM calendar_bls_releases r JOIN calendar_events e ON e.id=r.event_id
					WHERE r.name=? AND r.period=?`, row.name, row.period).Scan(&id, &uid)
				if err != nil || uid != row.uid || (first == "ics" && id != Key("bls:"+row.uid)) {
					t.Errorf("%s: wrong event %s, UID %s: %v", row.name, id, uid, err)
				}
			}
			var count int
			if err := s.DB.QueryRow("SELECT count(*) FROM calendar_events").Scan(&count); err != nil || count != 11 {
				t.Fatalf("got %d events, want 11: %v", count, err)
			}
		})
	}
}

func TestBLSUnqualifiedProductivityRequiresUniqueReleaseTime(t *testing.T) {
	s := testStore(t)
	const now = 1800000000
	if err := s.importBLSTestPackage(t, now); err != nil {
		t.Fatal(err)
	}
	file := []byte(blsReleaseICS("unspecified", "Productivity and Costs", "20260108T140000Z"))
	preview, err := s.ImportBLSFile(file, "", now)
	if err != nil || len(preview.Changes) != 1 || preview.Changes[0].Action != "add" {
		t.Fatal("different time must not bind either stage", preview, err)
	}
	// Move the explicitly identified revision to the preliminary release's instant.
	revision := blsReleaseICS("revised", "Productivity and Costs (R) for Third Quarter 2025", "20260108T133000Z")
	events, err := Parse("bls", blsICSURL, revision, 0)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Import("bls", events, now, nil); err != nil {
		t.Fatal(err)
	}
	file = []byte(blsReleaseICS("unspecified", "Productivity and Costs", "20260108T133000Z"))
	if _, err := s.ImportBLSFile(file, "", now); !errors.Is(err, errAmbiguousRelease) {
		t.Fatalf("two possible stages: got %v, want ambiguity", err)
	}
}

func TestICSUploadReviewCommitAndReplay(t *testing.T) {
	s := testStore(t)
	const now = 1800000000
	if err := s.Import("bls", nil, now, errors.New("HTTP 403")); err != nil {
		t.Fatal(err)
	}
	if err := s.importBLSTestPackage(t, now); err != nil {
		t.Fatal(err)
	}
	file := fixture(t, "bls-import-example.ics")
	original, err := s.List("2026-12-04", "2026-12-04")
	if err != nil || len(original) != 1 {
		t.Fatal(original, err)
	}
	id := original[0].ID
	run := func(body, key string) ICSImport {
		t.Helper()
		got, err := s.ImportBLSFile([]byte(body), key, now)
		if err != nil {
			t.Fatal(err)
		}
		return got
	}
	// The test notice moved this release to 09:30 Eastern. Confirm that instant in UTC.
	file = strings.Replace(file, ";TZID=America/New_York:20261204T083000", ":20261204T143000Z", 1)
	first := run(file, "")
	if len(first.Changes) != 1 || first.Changes[0].Action != "update" {
		t.Fatal(first)
	}
	old, err := s.Get(id)
	if err != nil || old.Version != original[0].Version {
		t.Fatal(old, err)
	}
	run(file, first.Token)
	updated, err := s.Get(id)
	if err != nil || updated.Version != old.Version+1 || updated.Provenance.Mode != "upload" {
		t.Fatal(updated, err)
	}
	raw, err := s.SnapshotBody(updated.Provenance.SnapshotID)
	if err != nil || string(raw) != file {
		t.Fatal(err, string(raw))
	}
	variant := strings.Replace(file, "VERSION:2.0", "VERSION:2.0\nCALSCALE:GREGORIAN", 1)
	if equal := run(variant, ""); len(equal.Changes) != 0 || equal.Unchanged != 1 {
		t.Fatal(equal)
	}
	later := strings.Replace(file, "20261204T143000Z", "20261207T133000Z", 1)
	change := run(later, "")
	if len(change.Changes) != 1 || change.Changes[0].Action != "reschedule" {
		t.Fatal(change)
	}
	cancel := strings.Replace(file, "END:VEVENT", "STATUS:CANCELLED\nSEQUENCE:2\nEND:VEVENT", 1)
	run(cancel, run(cancel, "").Token)
	if _, err := s.ImportBLSFile([]byte(later), change.Token, now); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	if stale := run(later, ""); stale.Stale != 1 || len(stale.Changes) != 0 {
		t.Fatal(stale)
	}
	if repeat := run(file, first.Token); !repeat.AlreadyImported {
		t.Fatal(repeat)
	}
	got, err := s.Get(id)
	if err != nil || got.Status != "cancelled" || got.Version != updated.Version+1 {
		t.Fatal(got, err)
	}
	sources, err := s.Sources()
	if err != nil {
		t.Fatal(err)
	}
	for _, src := range sources {
		if src.ID == "bls" && (src.Error != "HTTP 403" || src.LastSuccess != 0 || src.Count != 0) {
			t.Fatal(src)
		}
	}
}

func TestICSUploadAllOrNothingAndNestedComponents(t *testing.T) {
	s := testStore(t)
	file := fixture(t, "bls-import-example.ics")
	nested := strings.Replace(file, "END:VEVENT", "BEGIN:VALARM\nDESCRIPTION:Alarm text\nEND:VALARM\nEND:VEVENT", 1)
	check, err := s.ImportBLSFile([]byte(nested), "", 100)
	if err != nil || len(check.Changes) != 1 || check.Changes[0].Action != "add" {
		t.Fatal(check, err)
	}
	if _, err := s.ImportBLSFile([]byte(nested), "wrong", 100); !errors.Is(err, ErrConflict) {
		t.Fatal(err)
	}
	var count int
	if err = s.DB.QueryRow(`SELECT count(*) FROM calendar_events`).Scan(&count); err != nil || count != 0 {
		t.Fatal(count, err)
	}
	for _, bad := range []string{
		strings.Replace(file, "END:VCALENDAR", "", 1),
		strings.Replace(file, "END:VEVENT", "BEGIN:VEVENT\nEND:VEVENT", 1),
		strings.Replace(file, "END:VEVENT", "RRULE:FREQ=WEEKLY\nEND:VEVENT", 1),
		strings.Replace(file, "END:VEVENT", "UID:duplicate\nEND:VEVENT", 1),
		strings.Replace(file, "20261204T083000", "invalid", 1), "<html>403</html>",
	} {
		if _, err = s.ImportBLSFile([]byte(bad), "", 100); !errors.Is(err, ErrInvalidICS) {
			t.Fatal(err)
		}
	}
	if _, err = s.ImportBLSFile([]byte(nested), check.Token, 100); err != nil {
		t.Fatal(err)
	}
	list, err := s.List("2026-12-04", "2026-12-04")
	if err != nil || len(list) != 1 || strings.Contains(list[0].Evidence, "Alarm text") {
		t.Fatal(list, err)
	}
}
