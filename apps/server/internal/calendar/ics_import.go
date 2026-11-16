package calendar

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"unicode/utf8"
)

const MaxICSBytes = 2 << 20

var ErrInvalidICS = errors.New("无法导入 ICS")
var errAmbiguousRelease = errors.New("BLS 项目与统计期有多个候选，保留原日程")

type ICSTime struct {
	Start     string `json:"start"`
	End       string `json:"end"`
	Precision string `json:"precision"`
}

type ICSChange struct {
	Action         string   `json:"action"`
	Title          string   `json:"title"`
	PreviousTitle  string   `json:"previous_title,omitempty"`
	PreviousStatus string   `json:"previous_status,omitempty"`
	Status         string   `json:"status"`
	Before         *ICSTime `json:"before,omitempty"`
	After          ICSTime  `json:"after"`
}

type ICSImport struct {
	Token           string      `json:"token"`
	Total           int         `json:"total"`
	Unchanged       int         `json:"unchanged"`
	Stale           int         `json:"stale"`
	AlreadyImported bool        `json:"already_imported"`
	Changes         []ICSChange `json:"changes"`
}

func olderICS(old, next Event) bool {
	if old.Source != "bls" || old.SourceID != next.SourceID {
		return false
	}
	return next.ICSSequence < old.ICSSequence || (next.ICSSequence == old.ICSSequence &&
		next.ICSModified != "" && old.ICSModified != "" && next.ICSModified < old.ICSModified)
}

// Preview and confirmation use the same matching rules. A confirmation is valid
// only for these bytes and the versions of the events the user reviewed.
func (s *Store) ImportBLSFile(body []byte, confirmation string, now int64) (ICSImport, error) {
	result := ICSImport{Changes: []ICSChange{}}
	if len(body) > MaxICSBytes || !utf8.Valid(body) {
		return result, fmt.Errorf("%w：请选择 2 MiB 以内的 UTF-8 日历文件", ErrInvalidICS)
	}
	events, err := Parse("bls", blsICSURL, string(body), 0)
	if err != nil {
		return result, fmt.Errorf("%w：%v", ErrInvalidICS, err)
	}
	result.Total = len(events)
	digest := fmt.Sprintf("%x", sha256.Sum256(body))
	fileID := "bls-upload-" + digest
	tx, err := s.DB.Begin()
	if err != nil {
		return result, err
	}
	defer tx.Rollback()
	if err := tx.QueryRow(`SELECT EXISTS(SELECT 1 FROM calendar_snapshots WHERE id=?)`, fileID).Scan(&result.AlreadyImported); err != nil {
		return result, err
	}
	if result.AlreadyImported {
		result.Unchanged = result.Total
		return result, nil
	}

	review := sha256.New()
	fmt.Fprintln(review, digest)
	seen := make(map[string]bool)
	info := SnapshotInfo{ID: fileID, Format: "ics", URL: blsICSURL, SHA256: digest, ImportedAt: now, Entries: len(events)}
	provenance := &Provenance{Mode: "upload", Format: "ics", SnapshotID: fileID, SHA256: digest, ReviewedAt: now}
	for _, e := range events {
		id, err := matchBLSRelease(tx, e)
		if err != nil {
			if errors.Is(err, errAmbiguousRelease) {
				return result, fmt.Errorf("%w：%w", ErrInvalidICS, err)
			}
			return result, err
		}
		if id == "" {
			id = Key("bls:" + e.SourceID)
		}
		if seen[id] {
			return result, fmt.Errorf("%w：多个 UID 指向同一日程，请核对文件", ErrInvalidICS)
		}
		seen[id] = true
		old, err := get(tx, id)
		if err != nil && err != sql.ErrNoRows {
			return result, err
		}
		fmt.Fprintln(review, id, old.Version)
		_, end := e.Days()
		if end > info.CoverageEnd {
			info.CoverageEnd = end
		}
		if olderICS(old, e) {
			result.Stale++
			continue
		}
		if err := includeBenchmarkNote(tx, id, &e); err != nil {
			return result, err
		}
		change := ICSChange{Title: e.Title, Action: "add", After: ICSTime{e.Start, e.End, e.Precision}}
		if old.ID != "" {
			e.ID, e.Version, e.UpdatedAt = old.ID, old.Version, old.UpdatedAt
			e.Status = revisionStatus(old, e)
			// An unchanged event in a newer file keeps its existing evidence.
			e.Provenance = old.Provenance
			if old == e {
				result.Unchanged++
				continue
			}
			before := ICSTime{old.Start, old.End, old.Precision}
			change.Before, change.Action = &before, "update"
			change.PreviousStatus = old.Status
			if old.Title != e.Title {
				change.PreviousTitle = old.Title
			}
			if scheduleMoved(old, e) {
				change.Action = "reschedule"
			}
		}
		if e.Status == "cancelled" && old.Status != "cancelled" {
			change.Action = "cancel"
		}
		change.Status = e.Status
		result.Changes = append(result.Changes, change)
		if confirmation != "" {
			e.Provenance = provenance
			if _, err := saveAs(tx, e, id, old.Version, now); err != nil {
				return result, err
			}
			if err := rememberBLSAlias(tx, "bls", e.SourceID, id); err != nil {
				return result, err
			}
		}
	}
	result.Token = hex.EncodeToString(review.Sum(nil))
	if confirmation == "" {
		return result, nil
	}
	if result.Token != confirmation {
		return result, ErrConflict
	}
	if len(result.Changes) == 0 {
		return result, nil
	}
	info.ImportedEntries = len(result.Changes)
	raw, err := json.Marshal(info)
	if err != nil {
		return result, err
	}
	if _, err := tx.Exec(`INSERT INTO calendar_snapshots VALUES(?,'bls',?,?)`, fileID, string(raw), body); err != nil {
		return result, err
	}
	return result, tx.Commit()
}
