package probe

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/url"
	"os"
	"path/filepath"
	"time"
)

const trendStaleSeconds = 40 * 60

type trendCycle struct {
	Day     string
	ID      int64
	At      int64
	Sources []trendStatus
}
type trendStatus struct{ Source, Status string }

func initSourceHealth(db *sql.DB) error {
	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS trend_health_sync(id INTEGER PRIMARY KEY CHECK(id=1), attempted INTEGER NOT NULL, succeeded INTEGER NOT NULL, error TEXT NOT NULL, warnings TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trend_health_cycles(day TEXT NOT NULL, record_id INTEGER NOT NULL, at INTEGER NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(day,record_id));
CREATE INDEX IF NOT EXISTS trend_health_cycle_time ON trend_health_cycles(at);
CREATE TABLE IF NOT EXISTS trend_source_observations(id INTEGER PRIMARY KEY, day TEXT NOT NULL, record_id INTEGER NOT NULL, source TEXT NOT NULL, at INTEGER NOT NULL, status TEXT NOT NULL, UNIQUE(day,record_id,source), FOREIGN KEY(day,record_id) REFERENCES trend_health_cycles(day,record_id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS trend_source_time ON trend_source_observations(source,at);
CREATE INDEX IF NOT EXISTS trend_failure_time ON trend_source_observations(status,at,id);
CREATE TABLE IF NOT EXISTS trend_source_incidents(id INTEGER PRIMARY KEY, day TEXT NOT NULL, record_id INTEGER NOT NULL, source TEXT NOT NULL, at INTEGER NOT NULL, status TEXT NOT NULL, UNIQUE(day,record_id,source,status), FOREIGN KEY(day,record_id) REFERENCES trend_health_cycles(day,record_id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS trend_incident_time ON trend_source_incidents(at,id);
CREATE INDEX IF NOT EXISTS trend_incident_source ON trend_source_incidents(source,at);`)
	return err
}

// One read transaction per daily database keeps crawl records and source statuses consistent.
// No immutable=1: TrendRadar may be writing its WAL concurrently.
func readTrendHealthDay(ctx context.Context, path, day string, now time.Time) ([]trendCycle, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	uriPath := filepath.ToSlash(absolute)
	if filepath.VolumeName(absolute) != "" {
		uriPath = "/" + uriPath
	}
	u := url.URL{Scheme: "file", Path: uriPath, RawQuery: "mode=ro&_pragma=busy_timeout%28500%29"}
	db, err := sql.Open("sqlite", u.String())
	if err != nil {
		return nil, err
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	tx, err := db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	rows, err := tx.QueryContext(ctx, `SELECT r.id,r.crawl_time,s.platform_id,s.status FROM crawl_records r LEFT JOIN crawl_source_status s ON s.crawl_record_id=r.id ORDER BY r.id,s.platform_id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	zone, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return nil, err
	}
	cycles := []trendCycle{}
	count := 0
	for rows.Next() {
		count++
		if count > 10000 {
			return nil, errors.New("unexpected source status volume")
		}
		var id int64
		var clock string
		var source, status sql.NullString
		if err = rows.Scan(&id, &clock, &source, &status); err != nil {
			return nil, err
		}
		at, e := time.ParseInLocation("2006-01-02 15-04", day+" "+clock, zone)
		if e != nil || at.After(now) || id <= 0 {
			return nil, errors.New("invalid crawl identity or time")
		}
		if len(cycles) == 0 || cycles[len(cycles)-1].ID != id {
			cycles = append(cycles, trendCycle{Day: day, ID: id, At: at.Unix(), Sources: []trendStatus{}})
		}
		cycle := &cycles[len(cycles)-1]
		if !source.Valid {
			continue
		} // An empty cycle is evidence of missing source records.
		if source.String == "" || len(source.String) > 128 || len(cycle.Sources) >= 100 {
			return nil, errors.New("invalid source identity or count")
		}
		if len(cycle.Sources) > 0 && cycle.Sources[len(cycle.Sources)-1].Source == source.String {
			return nil, errors.New("duplicate source status")
		}
		value := "unknown"
		if status.String == "success" || status.String == "failed" {
			value = status.String
		}
		cycle.Sources = append(cycle.Sources, trendStatus{source.String, value})
	}
	if err = rows.Err(); err != nil {
		return nil, err
	}
	if err = rows.Close(); err != nil {
		return nil, err
	}
	return cycles, tx.Commit()
}

func readTrendHealth(ctx context.Context, root string, now time.Time) ([]trendCycle, []string, error) {
	zone, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return nil, nil, err
	}
	cycles, warnings := []trendCycle{}, []string{}
	found := false
	for _, offset := range []int{-1, 0} {
		day := now.In(zone).AddDate(0, 0, offset).Format("2006-01-02")
		path := filepath.Join(root, "news", day+".db")
		if _, err = os.Stat(path); os.IsNotExist(err) {
			warnings = append(warnings, day+" 数据库尚未读到，该日记录未补齐。")
			continue
		} else if err != nil {
			return nil, warnings, err
		}
		found = true
		part, e := readTrendHealthDay(ctx, path, day, now)
		if e != nil {
			return nil, warnings, e
		}
		cycles = append(cycles, part...)
	}
	if !found {
		return nil, warnings, errors.New("today/yesterday source status database not found")
	}
	return cycles, warnings, nil
}

func (s *Store) saveTrendHealth(ctx context.Context, cycles []trendCycle, warnings []string, now int64, readErr error) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	message := ""
	if readErr != nil {
		message = "无法读取 TrendRadar 来源状态，请检查数据库版本、状态表或读取权限。"
	}
	encoded, _ := json.Marshal(warnings)
	_, err = tx.ExecContext(ctx, `INSERT INTO trend_health_sync VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET attempted=excluded.attempted,succeeded=CASE WHEN excluded.error='' THEN excluded.succeeded ELSE succeeded END,error=excluded.error,warnings=excluded.warnings`, now, func() int64 {
		if readErr != nil {
			return 0
		}
		return now
	}(), message, string(encoded))
	if err != nil {
		return err
	}
	if readErr == nil {
		for _, cycle := range cycles {
			encoded, _ := json.Marshal(cycle)
			hash := digest(string(encoded))
			var previous string
			err = tx.QueryRowContext(ctx, `SELECT hash FROM trend_health_cycles WHERE day=? AND record_id=?`, cycle.Day, cycle.ID).Scan(&previous)
			if err != nil && err != sql.ErrNoRows {
				return err
			}
			if previous == hash {
				continue
			}
			if _, err = tx.ExecContext(ctx, `INSERT INTO trend_health_cycles VALUES(?,?,?,?) ON CONFLICT(day,record_id) DO UPDATE SET at=excluded.at,hash=excluded.hash`, cycle.Day, cycle.ID, cycle.At, hash); err != nil {
				return err
			}
			if _, err = tx.ExecContext(ctx, `DELETE FROM trend_source_observations WHERE day=? AND record_id=?`, cycle.Day, cycle.ID); err != nil {
				return err
			}
			for _, source := range cycle.Sources {
				if _, err = tx.ExecContext(ctx, `INSERT INTO trend_source_observations(day,record_id,source,at,status) VALUES(?,?,?,?,?)`, cycle.Day, cycle.ID, source.Source, cycle.At, source.Status); err != nil {
					return err
				}
				// Preserve a recorded failure even if the upstream later revises this same cycle.
				if source.Status != "success" {
					if _, err = tx.ExecContext(ctx, `INSERT OR IGNORE INTO trend_source_incidents(day,record_id,source,at,status) VALUES(?,?,?,?,?)`, cycle.Day, cycle.ID, source.Source, cycle.At, source.Status); err != nil {
						return err
					}
				}
			}
		}
	}
	return tx.Commit()
}

func (a *App) syncTrendHealth(root string, now time.Time) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	cycles, warnings, readErr := readTrendHealth(ctx, root, now)
	cancel()
	if readErr != nil {
		log.Printf("TrendRadar source status: %v", readErr)
	}
	// Persist the read failure even if the bounded read timed out. News import is independent.
	ctx, cancel = context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := a.Store.saveTrendHealth(ctx, cycles, warnings, now.Unix(), readErr); err != nil {
		log.Printf("save TrendRadar source status: %v", err)
	}
}

type TrendSource struct {
	ID          string `json:"id"`
	State       string `json:"state"`
	LastAttempt int64  `json:"last_attempt"`
	LastStatus  string `json:"last_status"`
	LastSuccess int64  `json:"last_success"`
	LastFailure int64  `json:"last_failure"`
	RecoveredAt int64  `json:"recovered_at"`
	Stale       bool   `json:"stale"`
}
type TrendHealth struct {
	ServerTime        int64         `json:"server_time"`
	CollectionEnabled bool          `json:"collection_enabled"`
	TrendEnabled      bool          `json:"trendradar_enabled"`
	ReadAttempt       int64         `json:"read_attempt"`
	ReadSuccess       int64         `json:"read_success"`
	ReadError         string        `json:"read_error"`
	ReadStale         bool          `json:"read_stale"`
	Warnings          []string      `json:"warnings"`
	LatestCycle       int64         `json:"latest_cycle"`
	HistorySince      int64         `json:"history_since"`
	Sources           []TrendSource `json:"sources"`
}

func (s *Store) TrendHealth(ctx context.Context, now int64) (TrendHealth, error) {
	out := TrendHealth{ServerTime: now, Warnings: []string{}, Sources: []TrendSource{}}
	tx, err := s.DB.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return out, err
	}
	defer tx.Rollback()
	var warnings string
	err = tx.QueryRowContext(ctx, `SELECT attempted,succeeded,error,warnings FROM trend_health_sync WHERE id=1`).Scan(&out.ReadAttempt, &out.ReadSuccess, &out.ReadError, &warnings)
	if err != nil && err != sql.ErrNoRows {
		return out, err
	}
	if warnings != "" {
		if err = json.Unmarshal([]byte(warnings), &out.Warnings); err != nil {
			return out, err
		}
	}
	out.ReadStale = out.ReadSuccess == 0 || now-out.ReadSuccess > 180
	if err = tx.QueryRowContext(ctx, `SELECT COALESCE(MAX(at),0),COALESCE(MIN(at),0) FROM trend_health_cycles`).Scan(&out.LatestCycle, &out.HistorySince); err != nil {
		return out, err
	}
	var latestDay string
	var latestID int64
	err = tx.QueryRowContext(ctx, `SELECT day,record_id FROM trend_health_cycles ORDER BY at DESC,record_id DESC LIMIT 1`).Scan(&latestDay, &latestID)
	if err != nil && err != sql.ErrNoRows {
		return out, err
	}
	rows, err := tx.QueryContext(ctx, `WITH sources AS (SELECT source FROM trend_source_observations UNION SELECT source FROM trend_source_incidents)
SELECT source,COALESCE((SELECT MAX(at) FROM trend_source_observations o WHERE o.source=s.source),0),COALESCE((SELECT MAX(at) FROM trend_source_observations o WHERE o.source=s.source AND status='success'),0),COALESCE((SELECT MAX(at) FROM trend_source_incidents i WHERE i.source=s.source AND status='failed'),0) FROM sources s ORDER BY source`)
	if err != nil {
		return out, err
	}
	for rows.Next() {
		var source TrendSource
		if err = rows.Scan(&source.ID, &source.LastAttempt, &source.LastSuccess, &source.LastFailure); err != nil {
			rows.Close()
			return out, err
		}
		out.Sources = append(out.Sources, source)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return out, err
	}
	for i := range out.Sources {
		source := &out.Sources[i]
		var inLatest bool
		if err = tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM trend_source_observations WHERE day=? AND record_id=? AND source=?)`, latestDay, latestID, source.ID).Scan(&inLatest); err != nil {
			return out, err
		}
		source.LastStatus = "unknown"
		if err = tx.QueryRowContext(ctx, `SELECT status FROM trend_source_observations WHERE source=? ORDER BY at DESC,record_id DESC LIMIT 1`, source.ID).Scan(&source.LastStatus); err != nil && err != sql.ErrNoRows {
			return out, err
		}
		if source.LastFailure > 0 {
			if err = tx.QueryRowContext(ctx, `SELECT COALESCE(MIN(at),0) FROM trend_source_observations WHERE source=? AND status='success' AND at>?`, source.ID, source.LastFailure).Scan(&source.RecoveredAt); err != nil {
				return out, err
			}
		}
		source.Stale = now-source.LastAttempt > trendStaleSeconds
		source.State = source.LastStatus
		switch {
		case !inLatest:
			source.State = "missing"
		case source.LastStatus == "failed":
			source.State = "failed"
		case source.LastStatus != "success":
			source.State = "unknown"
		case source.Stale:
			source.State = "stale"
		case out.ReadStale || out.ReadError != "":
			source.State = "unknown"
		default:
			source.State = "healthy"
		}
	}
	return out, tx.Commit()
}

type TrendFailure struct {
	ID          int64  `json:"id"`
	Source      string `json:"source"`
	At          int64  `json:"at"`
	Status      string `json:"status"`
	RecoveredAt int64  `json:"recovered_at"`
	Corrected   bool   `json:"corrected"`
}
type TrendFailurePage struct {
	Items []TrendFailure `json:"items"`
	Next  string         `json:"next"`
}

func (s *Store) TrendFailures(ctx context.Context, source string, beforeAt, beforeID int64) (TrendFailurePage, error) {
	out := TrendFailurePage{Items: []TrendFailure{}}
	rows, err := s.DB.QueryContext(ctx, `SELECT o.id,o.source,o.at,o.status,COALESCE((SELECT MIN(r.at) FROM trend_source_observations r WHERE r.source=o.source AND r.status='success' AND r.at>o.at),0),NOT EXISTS(SELECT 1 FROM trend_source_observations r WHERE r.day=o.day AND r.record_id=o.record_id AND r.source=o.source AND r.status=o.status) FROM trend_source_incidents o WHERE (?='' OR o.source=?) AND (?=0 OR o.at<? OR (o.at=? AND o.id<?)) ORDER BY o.at DESC,o.id DESC LIMIT 31`, source, source, beforeAt, beforeAt, beforeAt, beforeID)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var row TrendFailure
		if err = rows.Scan(&row.ID, &row.Source, &row.At, &row.Status, &row.RecoveredAt, &row.Corrected); err != nil {
			return out, err
		}
		out.Items = append(out.Items, row)
	}
	if len(out.Items) > 30 {
		last := out.Items[29]
		out.Next = fmt.Sprintf("%d:%d", last.At, last.ID)
		out.Items = out.Items[:30]
	}
	return out, rows.Err()
}
