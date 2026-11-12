package calendar

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"database/sql"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"time"
)

type Provenance struct {
	Mode         string `json:"mode"`
	SnapshotID   string `json:"snapshot_id,omitempty"`
	RetrievedAt  int64  `json:"retrieved_at,omitempty"`
	ReviewedAt   int64  `json:"reviewed_at,omitempty"`
	SHA256       string `json:"sha256,omitempty"`
	AuthorityURL string `json:"authority_url,omitempty"`
}

type SnapshotInfo struct {
	ID              string `json:"id"`
	URL             string `json:"url"`
	RetrievedAt     int64  `json:"retrieved_at"`
	ReviewedAt      int64  `json:"reviewed_at"`
	SHA256          string `json:"sha256"`
	Entries         int    `json:"entries"`
	Year            int    `json:"year"`
	ImportedAt      int64  `json:"imported_at"`
	ImportedEntries int    `json:"imported_entries"`
	ActiveEntries   int    `json:"active_entries"`
	CoverageEnd     string `json:"coverage_end"`
}

//go:embed snapshots/nbs-2026.html.gz
var nbsSnapshot []byte

//go:embed snapshots/nbs-2026.json
var nbsSnapshotMetadata []byte

func bundledSnapshot() (SnapshotInfo, []byte, error) {
	var info SnapshotInfo
	if err := json.Unmarshal(nbsSnapshotMetadata, &info); err != nil {
		return info, nil, err
	}
	r, err := gzip.NewReader(bytes.NewReader(nbsSnapshot))
	if err != nil {
		return info, nil, err
	}
	defer r.Close()
	body, err := io.ReadAll(io.LimitReader(r, 2*1024*1024+1))
	return info, body, err
}

func (s *Store) ImportBundledSnapshot() error {
	info, body, err := bundledSnapshot()
	if err != nil {
		return err
	}
	return s.importSnapshot(info, body, time.Now().Unix())
}

func (s *Store) importSnapshot(info SnapshotInfo, body []byte, now int64) error {
	sum := sha256.Sum256(body)
	if len(body) > 2*1024*1024 || hex.EncodeToString(sum[:]) != info.SHA256 || info.ID != "nbs-2026-"+info.SHA256[:12] || info.Year != 2026 ||
		info.RetrievedAt <= 0 || info.ReviewedAt < info.RetrievedAt || info.ReviewedAt > now || info.URL != "https://www.stats.gov.cn/xw/tjxw/tzgg/202512/t20251224_1962137.html" {
		return errors.New("官方快照身份或核对记录无效")
	}
	events, err := Parse("nbs", info.URL, string(body), info.Year)
	if err != nil {
		return err
	}
	if len(events) != info.Entries || info.Entries != 47 {
		return errors.New("官方快照条数不符")
	}
	tx, err := s.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var exists int
	if err = tx.QueryRow(`SELECT COUNT(*) FROM calendar_snapshots WHERE id=?`, info.ID).Scan(&exists); err != nil {
		return err
	}
	if exists > 0 {
		return nil
	}
	var liveSuccess int64
	err = tx.QueryRow(`SELECT last_success FROM calendar_sources WHERE id='nbs'`).Scan(&liveSuccess)
	if err != nil && err != sql.ErrNoRows {
		return err
	}
	info.ImportedAt = now
	for _, e := range events {
		_, end := e.Days()
		if end > info.CoverageEnd {
			info.CoverageEnd = end
		}
		// A reviewed snapshot is only a bootstrap. Never overwrite an existing
		// record or fill from an older snapshot after a newer live synchronization.
		if liveSuccess > info.ReviewedAt {
			continue
		}
		_, err = get(tx, Key(e.Source+":"+e.SourceID))
		if err == nil {
			continue
		}
		if err != sql.ErrNoRows {
			return err
		}
		e.Provenance = &Provenance{Mode: "snapshot", SnapshotID: info.ID, RetrievedAt: info.RetrievedAt, ReviewedAt: info.ReviewedAt, SHA256: info.SHA256}
		if _, err = save(tx, e, 0, now); err != nil {
			return err
		}
		info.ImportedEntries++
	}
	metadata, err := json.Marshal(info)
	if err != nil {
		return err
	}
	_, err = tx.Exec(`INSERT INTO calendar_snapshots VALUES(?,'nbs',?,?)`, info.ID, string(metadata), body)
	if err != nil {
		return err
	}
	// Deliberately do not update calendar_sources: this is not a successful live poll.
	return tx.Commit()
}

func (s *Store) snapshotInfo(source string) ([]SnapshotInfo, error) {
	rows, err := s.DB.Query(`SELECT metadata,(SELECT COUNT(*) FROM calendar_events WHERE source=calendar_snapshots.source AND json_extract(payload,'$.provenance.snapshot_id')=calendar_snapshots.id) FROM calendar_snapshots WHERE source=? ORDER BY id`, source)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []SnapshotInfo
	for rows.Next() {
		var raw string
		var active int
		if err = rows.Scan(&raw, &active); err != nil {
			return nil, err
		}
		var info SnapshotInfo
		if err = json.Unmarshal([]byte(raw), &info); err != nil {
			return nil, err
		}
		info.ActiveEntries = active
		result = append(result, info)
	}
	return result, rows.Err()
}

func (s *Store) SnapshotBody(id string) ([]byte, error) {
	var body []byte
	err := s.DB.QueryRow(`SELECT body FROM calendar_snapshots WHERE id=?`, id).Scan(&body)
	return body, err
}
