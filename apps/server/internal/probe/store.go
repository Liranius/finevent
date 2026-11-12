package probe

import (
	"database/sql"
	"fmt"
	"net/url"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"finevent/internal/calendar"

	_ "modernc.org/sqlite"
)

type Store struct{ DB *sql.DB }

func OpenStore(dir string) (*Store, error) {
	db, err := sql.Open("sqlite", filepath.Join(dir, "finevent.db"))
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	_, err = db.Exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON; PRAGMA cache_size=-2048;
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS subscriptions(id INTEGER PRIMARY KEY, endpoint TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, label TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY, subscription_id INTEGER NOT NULL REFERENCES subscriptions(id), scenario TEXT NOT NULL, status TEXT NOT NULL, due INTEGER NOT NULL, expires INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, accepted_at INTEGER NOT NULL DEFAULT 0, received_at INTEGER NOT NULL DEFAULT 0, clicked_at INTEGER NOT NULL DEFAULT 0, receipt_hash TEXT NOT NULL, receipt_token TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', manual_result TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS news(id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, url TEXT NOT NULL, published_at INTEGER NOT NULL, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, hash TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, baseline INTEGER NOT NULL DEFAULT 0, source_observed_at INTEGER NOT NULL DEFAULT 0, UNIQUE(source,source_id));
CREATE TABLE IF NOT EXISTS news_revisions(news_id INTEGER NOT NULL REFERENCES news(id) ON DELETE CASCADE, version INTEGER NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, saved_at INTEGER NOT NULL, PRIMARY KEY(news_id,version));
CREATE TABLE IF NOT EXISTS polls(id INTEGER PRIMARY KEY, source TEXT NOT NULL, started INTEGER NOT NULL, completed INTEGER NOT NULL, success INTEGER NOT NULL, added INTEGER NOT NULL, changed INTEGER NOT NULL, gap TEXT NOT NULL, error TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS polls_source_time ON polls(source,started);
CREATE TABLE IF NOT EXISTS resources(at INTEGER PRIMARY KEY, rss INTEGER NOT NULL, go_sys INTEGER NOT NULL, available INTEGER NOT NULL, cgroup_current INTEGER NOT NULL, cgroup_peak INTEGER NOT NULL, oom_kills INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ai_probe_runs(id TEXT PRIMARY KEY, month TEXT NOT NULL, started INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, reserved_nano INTEGER NOT NULL, cost_upper_nano INTEGER, prompt_tokens INTEGER, cached_tokens INTEGER, completion_tokens INTEGER, http_status INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER NOT NULL DEFAULT 0, pricing TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_probe_timings(id TEXT PRIMARY KEY REFERENCES ai_probe_runs(id), model_started_ms INTEGER NOT NULL DEFAULT 0, model_completed_ms INTEGER NOT NULL DEFAULT 0, collector_started_ms INTEGER NOT NULL DEFAULT 0, collector_completed_ms INTEGER NOT NULL DEFAULT 0, collector_success INTEGER NOT NULL DEFAULT 0);
`)
	if err != nil {
		db.Close()
		return nil, err
	}
	if err = calendar.Init(db); err != nil {
		db.Close()
		return nil, err
	}
	if err = initReminders(db); err != nil {
		db.Close()
		return nil, err
	}
	if err = initNews(db); err != nil {
		db.Close()
		return nil, err
	}
	if err = initCalendarLinks(db); err != nil {
		db.Close()
		return nil, err
	}
	if err = initNewsPush(db); err != nil {
		db.Close()
		return nil, err
	}
	if err = initSourceHealth(db); err != nil {
		db.Close()
		return nil, err
	}
	if err = initAnalysis(db); err != nil {
		db.Close()
		return nil, err
	}
	if err = initCollectionEvidence(db); err != nil {
		db.Close()
		return nil, err
	}
	// A process can die after claiming a delivery. Receivers deduplicate using its stable ID.
	if _, err = db.Exec(`UPDATE deliveries SET status='queued' WHERE status='sending'`); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{db}, nil
}

type Item struct {
	ID               int64  `json:"id"`
	Source           string `json:"source"`
	SourceID         string `json:"source_id"`
	Title            string `json:"title"`
	Content          string `json:"content"`
	URL              string `json:"url"`
	PublishedAt      int64  `json:"published_at"`
	FirstSeen        int64  `json:"first_seen"`
	LastSeen         int64  `json:"last_seen"`
	Version          int    `json:"version"`
	Baseline         bool   `json:"baseline"`
	SourceObservedAt int64  `json:"source_observed_at"`
}
type Poll struct {
	Source    string         `json:"source"`
	Started   int64          `json:"started"`
	Completed int64          `json:"completed"`
	Success   bool           `json:"success"`
	Added     int            `json:"added"`
	Changed   int            `json:"changed"`
	Gap       string         `json:"gap"`
	Error     string         `json:"error"`
	Requests  []SourceWindow `json:"-"`
}

func CanonicalURL(raw string) string {
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil {
		return ""
	}
	u.Fragment = ""
	u.Host = strings.ToLower(u.Host)
	q := u.Query()
	for k := range q {
		if strings.HasPrefix(strings.ToLower(k), "utm_") {
			q.Del(k)
		}
	}
	u.RawQuery = q.Encode()
	return u.String()
}

func (s *Store) SaveItems(items []Item, now int64) (int, int, error) {
	tx, err := s.DB.Begin()
	if err != nil {
		return 0, 0, err
	}
	defer tx.Rollback()
	added, changed := 0, 0
	for _, i := range items {
		if i.SourceID == "" || strings.TrimSpace(i.Title) == "" {
			continue
		}
		i.URL = CanonicalURL(i.URL)
		h := digest(fmt.Sprintf("%s\x00%s\x00%s\x00%d", i.Title, i.Content, i.URL, i.PublishedAt))
		var id int64
		var old string
		var observed int64
		err = tx.QueryRow(`SELECT id,hash,source_observed_at FROM news WHERE source=? AND source_id=?`, i.Source, i.SourceID).Scan(&id, &old, &observed)
		if err == nil && i.SourceObservedAt > 0 && observed > i.SourceObservedAt {
			continue
		}
		if err == sql.ErrNoRows {
			if i.FirstSeen == 0 {
				i.FirstSeen = now
			}
			_, err = tx.Exec(`INSERT INTO news(source,source_id,title,content,url,published_at,first_seen,last_seen,hash,baseline,source_observed_at,ingested_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`, i.Source, i.SourceID, i.Title, i.Content, i.URL, i.PublishedAt, i.FirstSeen, now, h, i.Baseline, i.SourceObservedAt, now, now)
			if err == nil {
				// The compatibility trigger may advance a reused rowid after retention cleanup.
				err = tx.QueryRow(`SELECT id FROM news WHERE source=? AND source_id=?`, i.Source, i.SourceID).Scan(&id)
			}
			if err == nil {
				err = indexNews(tx, id, now)
			}
			added++
		} else if err == nil && old != h {
			_, err = tx.Exec(`INSERT INTO news_revisions(news_id,version,title,content,saved_at) SELECT id,version,title,content,? FROM news WHERE id=?`, now, id)
			if err == nil {
				_, err = tx.Exec(`INSERT INTO news_revision_evidence SELECT id,version,url,published_at FROM news WHERE id=?`, id)
			}
			if err == nil {
				_, err = tx.Exec(`UPDATE news SET title=?,content=?,url=?,published_at=?,last_seen=?,hash=?,source_observed_at=?,version=version+1,updated_at=? WHERE id=?`, i.Title, i.Content, i.URL, i.PublishedAt, now, h, i.SourceObservedAt, now, id)
			}
			if err == nil {
				err = indexNews(tx, id, now)
			}
			changed++
		} else if err == nil {
			_, err = tx.Exec(`UPDATE news SET last_seen=?,source_observed_at=MAX(source_observed_at,?) WHERE id=?`, now, i.SourceObservedAt, id)
		}
		if err != nil {
			return 0, 0, err
		}
	}
	return added, changed, tx.Commit()
}

type Latency struct {
	Source      string  `json:"source"`
	Samples     int     `json:"samples"`
	P50         int64   `json:"p50_seconds"`
	P95         int64   `json:"p95_seconds"`
	Within5     float64 `json:"within_5_minutes"`
	Unknown     int     `json:"unknown_publication"`
	ClockErrors int     `json:"clock_errors"`
}

func (s *Store) Latencies(since int64) ([]Latency, error) {
	// Use the publication time actually seen on first discovery. Later source edits
	// must not rewrite historical latency; legacy revisions without evidence stay unknown.
	rows, err := s.DB.Query(`SELECT n.source,CASE WHEN n.version=1 THEN n.published_at ELSE COALESCE(e.published_at,0) END,n.first_seen
FROM news n LEFT JOIN news_revision_evidence e ON e.news_id=n.id AND e.version=1
WHERE n.first_seen>=? AND n.baseline=0 ORDER BY n.source LIMIT 50000`, since)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	values := map[string][]int64{}
	stats := map[string]*Latency{}
	for rows.Next() {
		var source string
		var pub, seen int64
		if err = rows.Scan(&source, &pub, &seen); err != nil {
			return nil, err
		}
		if stats[source] == nil {
			stats[source] = &Latency{Source: source}
		}
		if pub == 0 {
			stats[source].Unknown++
			continue
		}
		if seen < pub {
			stats[source].ClockErrors++
			continue
		}
		values[source] = append(values[source], seen-pub)
	}
	result := []Latency{}
	for source, stat := range stats {
		v := values[source]
		sort.Slice(v, func(i, j int) bool { return v[i] < v[j] })
		stat.Samples = len(v)
		if len(v) > 0 {
			stat.P50 = v[(len(v)-1)/2]
			stat.P95 = v[(len(v)*95+99)/100-1]
			n := sort.Search(len(v), func(i int) bool { return v[i] > 300 })
			stat.Within5 = float64(n) / float64(len(v))
		}
		result = append(result, *stat)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Source < result[j].Source })
	return result, rows.Err()
}

func (s *Store) Prune(now time.Time) error {
	tx, err := s.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, job := range []struct {
		query  string
		cutoff int64
	}{
		{`DELETE FROM sessions WHERE expires<?`, now.Unix()},
		{`DELETE FROM deliveries WHERE expires<?`, now.AddDate(0, 0, -30).Unix()},
		{`DELETE FROM news_push_events WHERE created<?`, now.AddDate(0, 0, -30).Unix()},
		{`DELETE FROM news WHERE last_seen<?`, now.AddDate(0, 0, -90).Unix()},
		{`DELETE FROM news_changes WHERE changed_at<?`, now.AddDate(0, 0, -90).Unix()},
		{`DELETE FROM polls WHERE completed<?`, now.AddDate(0, 0, -30).Unix()},
		{`DELETE FROM poll_requests WHERE poll_id IN (SELECT id FROM polls WHERE completed<?)`, now.AddDate(0, 0, -7).Unix()},
		{`DELETE FROM trend_health_cycles WHERE at<?`, now.AddDate(0, 0, -30).Unix()},
		{`DELETE FROM resources WHERE at<?`, now.AddDate(0, 0, -7).Unix()},
		{`DELETE FROM ai_jobs WHERE expires<? AND status<>'running'`, now.Unix()},
	} {
		if _, err = tx.Exec(job.query, job.cutoff); err != nil {
			return err
		}
	}
	return tx.Commit()
}
