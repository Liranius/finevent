package probe

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"strconv"
	"time"
)

// Keep bounded response metadata, not source bodies, credentials or request URLs.
// An absent ID only describes these observed windows, never the entire upstream feed.
type SourceWindow struct {
	StartedMS      int64          `json:"started_ms"`
	CompletedMS    int64          `json:"completed_ms"`
	Cursor         int64          `json:"cursor"`
	HTTPStatus     int            `json:"http_status"`
	ErrorKind      string         `json:"error_kind"`
	RetryAfter     int64          `json:"retry_after"`
	BodyBytes      int            `json:"body_bytes"`
	BodyComplete   bool           `json:"body_complete"`
	BodySHA256     string         `json:"body_sha256"`
	ItemCount      int            `json:"item_count"`
	ItemsTruncated bool           `json:"items_truncated"`
	Items          []ObservedItem `json:"items"`
}

type ObservedItem struct {
	ID          int64 `json:"id"`
	PublishedAt int64 `json:"published_at"`
}

func initCollectionEvidence(db *sql.DB) error {
	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS poll_requests(
poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
page INTEGER NOT NULL, error_kind TEXT NOT NULL, retry_after INTEGER NOT NULL,
evidence TEXT NOT NULL, PRIMARY KEY(poll_id,page));`)
	return err
}

func (s *Store) SavePoll(p Poll) error {
	tx, err := s.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	res, err := tx.Exec(`INSERT INTO polls(source,started,completed,success,added,changed,gap,error) VALUES(?,?,?,?,?,?,?,?)`, p.Source, p.Started, p.Completed, p.Success, p.Added, p.Changed, p.Gap, p.Error)
	if err != nil {
		return err
	}
	id, err := res.LastInsertId()
	if err != nil {
		return err
	}
	for page, request := range p.Requests {
		body, err := json.Marshal(request)
		if err != nil {
			return err
		}
		if _, err = tx.Exec(`INSERT INTO poll_requests VALUES(?,?,?,?,?)`, id, page, request.ErrorKind, request.RetryAfter, string(body)); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func transientSourceError(kind string) bool {
	switch kind {
	case "timeout", "network", "body_read", "server", "rate_limit":
		return true
	}
	return false
}

// Scheduling is reconstructed from persisted polls so a restart cannot reset backoff.
// No sleeps or immediate in-request retries: each due cycle remains a bounded collection.
func (s *Store) wallstreetDue(now time.Time, interval time.Duration) bool {
	rows, err := s.DB.Query(`SELECT p.completed,p.success,p.gap,COALESCE(r.error_kind,''),COALESCE(r.retry_after,0)
FROM polls p LEFT JOIN poll_requests r ON r.poll_id=p.id AND r.page=(SELECT MAX(page) FROM poll_requests WHERE poll_id=p.id)
WHERE p.source='wallstreetcn' ORDER BY p.id DESC LIMIT 5`)
	if err != nil {
		return false
	}
	defer rows.Close()
	var completed, retryAfter int64
	var success bool
	var gap, kind string
	failures := 0
	for rows.Next() {
		var at, retry int64
		var ok bool
		var missing, outcome string
		if err = rows.Scan(&at, &ok, &missing, &outcome, &retry); err != nil {
			return false
		}
		if completed == 0 {
			completed, success, gap, kind, retryAfter = at, ok, missing, outcome, retry
		}
		if ok && missing == "" {
			break
		}
		failures++
	}
	if rows.Err() != nil {
		return false
	}
	if completed == 0 {
		return true
	}
	if !success || gap != "" {
		if transientSourceError(kind) {
			interval = min(30*time.Second<<max(0, failures-1), 5*time.Minute)
			if kind == "rate_limit" {
				interval = max(interval, 5*time.Minute)
			}
		} else if !success {
			// Old polls lack classification; malformed responses and permanent HTTP errors
			// retain the existing conservative schedule.
			interval = max(interval, 5*time.Minute)
		}
	}
	return now.Unix() >= max(completed+int64(interval.Seconds()), retryAfter)
}

type CollectionPoll struct {
	ID int64 `json:"id"`
	Poll
	Requests []SourceWindow `json:"requests"`
}

func (s *Store) collectionHistory(before int64) ([]CollectionPoll, error) {
	rows, err := s.DB.Query(`SELECT id,source,started,completed,success,added,changed,gap,error FROM polls
WHERE source='wallstreetcn' AND (?=0 OR id<?) ORDER BY id DESC LIMIT 20`, before, before)
	if err != nil {
		return nil, err
	}
	result := []CollectionPoll{}
	for rows.Next() {
		p := CollectionPoll{Requests: []SourceWindow{}}
		if err = rows.Scan(&p.ID, &p.Source, &p.Started, &p.Completed, &p.Success, &p.Added, &p.Changed, &p.Gap, &p.Error); err != nil {
			rows.Close()
			return nil, err
		}
		result = append(result, p)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	for i := range result {
		requests, err := s.DB.Query(`SELECT evidence FROM poll_requests WHERE poll_id=? ORDER BY page`, result[i].ID)
		if err != nil {
			return nil, err
		}
		for requests.Next() {
			var body string
			var window SourceWindow
			if err = requests.Scan(&body); err == nil {
				err = json.Unmarshal([]byte(body), &window)
			}
			if err != nil {
				requests.Close()
				return nil, err
			}
			result[i].Requests = append(result[i].Requests, window)
		}
		err = requests.Err()
		requests.Close()
		if err != nil {
			return nil, err
		}
	}
	return result, nil
}

func (a *App) collectionHistory(w http.ResponseWriter, r *http.Request) {
	var before int64
	if raw := r.URL.Query().Get("before"); raw != "" {
		var err error
		before, err = strconv.ParseInt(raw, 10, 64)
		if err != nil || before <= 0 {
			fail(w, http.StatusBadRequest, "无效的采集游标")
			return
		}
	}
	polls, err := a.Store.collectionHistory(before)
	if err != nil {
		a.internal(w, err)
		return
	}
	var next int64
	if len(polls) == 20 {
		next = polls[len(polls)-1].ID
	}
	writeJSON(w, http.StatusOK, map[string]any{"polls": polls, "next": next})
}
