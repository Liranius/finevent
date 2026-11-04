// Package calendar owns scheduled events and human-confirmed fund windows.
package calendar

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strings"
	"time"
)

var ErrConflict = errors.New("记录已变更，请重新加载")

type Event struct {
	ID         string      `json:"id"`
	Source     string      `json:"source"`
	SourceID   string      `json:"source_id"`
	Title      string      `json:"title"`
	Kind       string      `json:"kind"`
	Start      string      `json:"start"`
	End        string      `json:"end"`
	Timezone   string      `json:"timezone"`
	Precision  string      `json:"precision"`
	Status     string      `json:"status"`
	Importance int         `json:"importance"`
	URL        string      `json:"url"`
	Evidence   string      `json:"evidence"`
	FundCode   string      `json:"fund_code"`
	Conditions string      `json:"conditions"`
	Version    int         `json:"version"`
	UpdatedAt  int64       `json:"updated_at"`
	Provenance *Provenance `json:"provenance,omitempty"`
}
type Revision struct {
	Event   Event `json:"event"`
	SavedAt int64 `json:"saved_at"`
}
type Source struct {
	ID          string         `json:"id"`
	Name        string         `json:"name"`
	URL         string         `json:"url"`
	LastAttempt int64          `json:"last_attempt"`
	LastSuccess int64          `json:"last_success"`
	Error       string         `json:"error"`
	Count       int            `json:"count"`
	CoverageEnd string         `json:"coverage_end"`
	Mode        string         `json:"mode,omitempty"`
	Note        string         `json:"note,omitempty"`
	Snapshots   []SnapshotInfo `json:"snapshots,omitempty"`
}
type Draft struct {
	ID          string   `json:"id"`
	Text        string   `json:"text"`
	URL         string   `json:"url"`
	Event       Event    `json:"event"`
	Warnings    []string `json:"warnings"`
	ConfirmedID string   `json:"confirmed_id"`
}
type Store struct{ DB *sql.DB }

func Init(db *sql.DB) error {
	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS calendar_events(id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL, start_day TEXT NOT NULL, end_day TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(source,source_id));
CREATE INDEX IF NOT EXISTS calendar_range ON calendar_events(start_day,end_day);
CREATE TABLE IF NOT EXISTS calendar_revisions(event_id TEXT NOT NULL REFERENCES calendar_events(id), version INTEGER NOT NULL, payload TEXT NOT NULL, saved_at INTEGER NOT NULL, PRIMARY KEY(event_id,version));
CREATE TABLE IF NOT EXISTS calendar_sources(id TEXT PRIMARY KEY, last_attempt INTEGER NOT NULL DEFAULT 0, last_success INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', count INTEGER NOT NULL DEFAULT 0, coverage_end TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS fund_drafts(id TEXT PRIMARY KEY, payload TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_snapshots(id TEXT PRIMARY KEY, source TEXT NOT NULL, metadata TEXT NOT NULL, body BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_source_repairs(id TEXT PRIMARY KEY, source TEXT NOT NULL, previous_state TEXT NOT NULL, requested_at INTEGER NOT NULL);`)
	return err
}
func Key(s string) string { h := sha256.Sum256([]byte(s)); return hex.EncodeToString(h[:16]) }

func ValidURL(s string) bool {
	u, err := url.Parse(s)
	return err == nil && (u.Scheme == "https" || u.Scheme == "http") && u.Hostname() != "" && u.User == nil && len(s) <= 2048
}
func (e Event) Validate() error {
	if strings.TrimSpace(e.Title) == "" || len(e.Title) > 600 || len(e.Evidence) > 12000 || len(e.Conditions) > 12000 || !ValidURL(e.URL) {
		return errors.New("标题、原文链接或文本长度无效")
	}
	if e.Kind != "macro" && e.Kind != "monetary" && e.Kind != "fund" && e.Kind != "other" {
		return errors.New("事件类型无效")
	}
	if e.Importance < 1 || e.Importance > 3 {
		return errors.New("重要性应为 1–3")
	}
	if e.Status != "scheduled" && e.Status != "confirmed" && e.Status != "rescheduled" && e.Status != "cancelled" {
		return errors.New("事件状态无效")
	}
	loc, err := time.LoadLocation(e.Timezone)
	if err != nil {
		return errors.New("时区无效")
	}
	if e.Precision == "date" {
		s, err := time.Parse("2006-01-02", e.Start)
		if err != nil {
			return errors.New("开始日期无效")
		}
		end, err := time.Parse("2006-01-02", e.End)
		if err != nil || end.Before(s) {
			return errors.New("结束日期应不早于开始日期")
		}
	} else if e.Precision == "minute" {
		s, err := time.Parse(time.RFC3339, e.Start)
		if err != nil {
			return errors.New("开始时间需含时区偏移")
		}
		end, err := time.Parse(time.RFC3339, e.End)
		if err != nil || end.Before(s) {
			return errors.New("结束时间无效")
		}
		for _, t := range []time.Time{s, end} {
			_, offset := t.Zone()
			_, expected := t.In(loc).Zone()
			if offset != expected {
				return errors.New("时间偏移与原始时区不一致")
			}
		}
	} else {
		return errors.New("时间精度无效")
	}
	if e.Kind == "fund" && (len(e.FundCode) != 6 || strings.Trim(e.FundCode, "0123456789") != "") {
		return errors.New("请确认六位基金代码；多份额需分别录入")
	}
	return nil
}

// Date-only values are civil dates, never shifted through UTC.
func (e Event) Days() (string, string) {
	if e.Precision == "date" {
		return e.Start, e.End
	}
	loc, _ := time.LoadLocation("Asia/Shanghai")
	s, _ := time.Parse(time.RFC3339, e.Start)
	end, _ := time.Parse(time.RFC3339, e.End)
	return s.In(loc).Format("2006-01-02"), end.In(loc).Format("2006-01-02")
}
func get(tx interface{ QueryRow(string, ...any) *sql.Row }, id string) (Event, error) {
	var e Event
	var b string
	err := tx.QueryRow(`SELECT payload FROM calendar_events WHERE id=?`, id).Scan(&b)
	if err == nil {
		err = json.Unmarshal([]byte(b), &e)
	}
	return e, err
}
func (s *Store) Get(id string) (Event, error) { return get(s.DB, id) }
func (s *Store) List(from, to string) ([]Event, error) {
	rows, err := s.DB.Query(`SELECT payload FROM calendar_events WHERE start_day<=? AND end_day>=? ORDER BY start_day,id`, to, from)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []Event{}
	for rows.Next() {
		var b string
		var e Event
		if err = rows.Scan(&b); err != nil {
			return nil, err
		}
		if err = json.Unmarshal([]byte(b), &e); err != nil {
			return nil, err
		}
		result = append(result, e)
	}
	return result, rows.Err()
}
func (s *Store) History(id string) ([]Revision, error) {
	rows, err := s.DB.Query(`SELECT payload,saved_at FROM calendar_revisions WHERE event_id=? ORDER BY version DESC`, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []Revision{}
	for rows.Next() {
		var b string
		var r Revision
		if err = rows.Scan(&b, &r.SavedAt); err != nil {
			return nil, err
		}
		if err = json.Unmarshal([]byte(b), &r.Event); err != nil {
			return nil, err
		}
		result = append(result, r)
	}
	return result, rows.Err()
}
func save(tx *sql.Tx, e Event, expected int, now int64) (Event, error) {
	if err := e.Validate(); err != nil {
		return e, err
	}
	e.ID = Key(e.Source + ":" + e.SourceID)
	old, err := get(tx, e.ID)
	exists := err == nil
	if err != nil && err != sql.ErrNoRows {
		return e, err
	}
	if expected >= 0 && old.Version != expected {
		return e, ErrConflict
	}
	if exists {
		if e.Status != "cancelled" && (old.Start != e.Start || old.End != e.End || old.Precision != e.Precision || old.Timezone != e.Timezone) {
			e.Status = "rescheduled"
		}
		if old.Status == "rescheduled" && e.Status == "scheduled" {
			e.Status = old.Status
		}
		e.Version = old.Version
		e.UpdatedAt = old.UpdatedAt
		a, _ := json.Marshal(e)
		b, _ := json.Marshal(old)
		if string(a) == string(b) {
			return old, nil
		}
	}
	e.Version = old.Version + 1
	e.UpdatedAt = now
	b, _ := json.Marshal(e)
	start, end := e.Days()
	_, err = tx.Exec(`INSERT INTO calendar_events VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET start_day=excluded.start_day,end_day=excluded.end_day,payload=excluded.payload`, e.ID, e.Source, e.SourceID, start, end, string(b))
	if err != nil {
		return e, err
	}
	_, err = tx.Exec(`INSERT INTO calendar_revisions VALUES(?,?,?,?)`, e.ID, e.Version, string(b), now)
	return e, err
}
func (s *Store) Save(e Event, expected int) (Event, error) {
	tx, err := s.DB.Begin()
	if err != nil {
		return e, err
	}
	defer tx.Rollback()
	e, err = save(tx, e, expected, time.Now().Unix())
	if err != nil {
		return e, err
	}
	return e, tx.Commit()
}
func (s *Store) Sources() ([]Source, error) {
	result := Sources()
	for i := range result {
		v := &result[i]
		err := s.DB.QueryRow(`SELECT last_attempt,last_success,error,count,coverage_end FROM calendar_sources WHERE id=?`, v.ID).Scan(&v.LastAttempt, &v.LastSuccess, &v.Error, &v.Count, &v.CoverageEnd)
		if err != nil && err != sql.ErrNoRows {
			return nil, err
		}
		v.Snapshots, err = s.snapshotInfo(v.ID)
		if err != nil {
			return nil, err
		}
	}
	return result, nil
}
func (s *Store) Import(source string, events []Event, now int64, fetchErr error) error {
	tx, err := s.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if fetchErr != nil {
		_, err = tx.Exec(`INSERT INTO calendar_sources(id,last_attempt,error) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET last_attempt=excluded.last_attempt,error=excluded.error`, source, now, fetchErr.Error())
	} else {
		end := ""
		for _, e := range events {
			if e.Source != source {
				return errors.New("source mismatch")
			}
			if _, err = save(tx, e, -1, now); err != nil {
				return err
			}
			_, d := e.Days()
			if d > end {
				end = d
			}
		}
		_, err = tx.Exec(`INSERT INTO calendar_sources VALUES(?,?,?,'',?,?) ON CONFLICT(id) DO UPDATE SET last_attempt=excluded.last_attempt,last_success=excluded.last_success,error='',count=excluded.count,coverage_end=excluded.coverage_end`, source, now, now, len(events), end)
	}
	if err != nil {
		return err
	}
	return tx.Commit()
}
func (s *Store) SaveDraft(d Draft) error {
	b, _ := json.Marshal(d)
	_, err := s.DB.Exec(`INSERT INTO fund_drafts VALUES(?,?,?)`, d.ID, string(b), time.Now().Unix())
	return err
}
func (s *Store) Draft(id string) (Draft, error) {
	var d Draft
	var b string
	err := s.DB.QueryRow(`SELECT payload FROM fund_drafts WHERE id=?`, id).Scan(&b)
	if err == nil {
		err = json.Unmarshal([]byte(b), &d)
	}
	return d, err
}
func (s *Store) Confirm(id string, e Event) (Event, error) {
	tx, err := s.DB.Begin()
	if err != nil {
		return e, err
	}
	defer tx.Rollback()
	var b string
	var d Draft
	if err = tx.QueryRow(`SELECT payload FROM fund_drafts WHERE id=?`, id).Scan(&b); err != nil {
		return e, err
	}
	if err = json.Unmarshal([]byte(b), &d); err != nil {
		return e, err
	}
	if d.ConfirmedID != "" {
		return get(tx, d.ConfirmedID)
	}
	e.Source = "fund"
	e.SourceID = id
	e.Kind = "fund"
	e.Precision = "date"
	e.Timezone = "Asia/Shanghai"
	e.Status = "confirmed"
	e.URL = d.URL
	e.Evidence = d.Text
	e.Provenance = nil
	e, err = save(tx, e, 0, time.Now().Unix())
	if err != nil {
		return e, err
	}
	d.ConfirmedID = e.ID
	d.Event = e
	b2, _ := json.Marshal(d)
	if _, err = tx.Exec(`UPDATE fund_drafts SET payload=? WHERE id=?`, string(b2), id); err != nil {
		return e, err
	}
	return e, tx.Commit()
}
func DateRange(from, to string) error {
	a, e := time.Parse("2006-01-02", from)
	b, e2 := time.Parse("2006-01-02", to)
	if e != nil || e2 != nil || b.Before(a) || b.Sub(a) > 366*24*time.Hour {
		return fmt.Errorf("查询范围须为不超过一年的有效日期")
	}
	return nil
}
