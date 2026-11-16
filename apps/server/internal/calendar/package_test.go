package calendar

import (
	"encoding/json"
	"os"
	"testing"
	"testing/fstest"
)

func testPackage(t *testing.T, source string) (packageManifest, fstest.MapFS) {
	t.Helper()
	b, err := os.ReadFile("testdata/manifest.json")
	if err != nil {
		t.Fatal(err)
	}
	var m packageManifest
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	files := fstest.MapFS{}
	var entries []packageEntry
	for _, e := range m.Snapshots {
		if source != "" && e.Source != source {
			continue
		}
		body, err := os.ReadFile("testdata/" + e.File)
		if err != nil {
			t.Fatal(err)
		}
		files[e.File] = &fstest.MapFile{Data: body}
		entries = append(entries, e)
	}
	m.Snapshots = entries
	b, err = json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	files["manifest.json"] = &fstest.MapFile{Data: b}
	return m, files
}

func (s *Store) importBLSTestPackage(t *testing.T, now int64) error {
	_, files := testPackage(t, "bls")
	return s.ImportCalendarPackage(files, now)
}

func testNBSSnapshot(t *testing.T) (SnapshotInfo, []byte) {
	m, files := testPackage(t, "nbs")
	e := m.Snapshots[0]
	return e.SnapshotInfo, files[e.File].Data
}

func importTestNBS(s *Store, info SnapshotInfo, body []byte, now int64) error {
	tx, err := s.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := importNBSSnapshot(tx, info, body, now); err != nil {
		return err
	}
	return tx.Commit()
}

func TestCalendarPackageRollsBackEverySourceOnInvalidLastFile(t *testing.T) {
	s := testStore(t)
	m, files := testPackage(t, "")
	last := files[m.Snapshots[len(m.Snapshots)-1].File]
	last.Data = append(last.Data, '!')
	if err := s.ImportCalendarPackage(files, 1800000000); err == nil {
		t.Fatal("accepted damaged final source")
	}
	for _, table := range []string{"calendar_events", "calendar_revisions", "calendar_package_reviews", "calendar_snapshots"} {
		var n int
		if err := s.DB.QueryRow("SELECT count(*) FROM " + table).Scan(&n); err != nil || n != 0 {
			t.Fatal(table, n, err)
		}
	}
}
