package probe

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"time"
)

func initAnalysis(db *sql.DB) error {
	_, err := db.Exec(`
CREATE TABLE IF NOT EXISTS ai_settings(singleton INTEGER PRIMARY KEY CHECK(singleton=1),enabled INTEGER NOT NULL DEFAULT 0,automatic INTEGER NOT NULL DEFAULT 0,cursor INTEGER NOT NULL DEFAULT 0,enabled_at INTEGER NOT NULL DEFAULT 0,not_before INTEGER NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO ai_settings(singleton) VALUES(1);
CREATE TABLE IF NOT EXISTS ai_jobs(id TEXT PRIMARY KEY,model TEXT NOT NULL,prompt_version TEXT NOT NULL,evidence TEXT NOT NULL,status TEXT NOT NULL,created INTEGER NOT NULL,completed INTEGER NOT NULL DEFAULT 0,expires INTEGER NOT NULL,result TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',attempts INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ai_jobs_queue ON ai_jobs(status,created);
CREATE INDEX IF NOT EXISTS ai_jobs_recent ON ai_jobs(created DESC,id DESC);
CREATE TABLE IF NOT EXISTS ai_links(news_id INTEGER NOT NULL,version INTEGER NOT NULL,job_id TEXT NOT NULL REFERENCES ai_jobs(id) ON DELETE CASCADE,PRIMARY KEY(news_id,version));
CREATE TABLE IF NOT EXISTS ai_calls(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,month TEXT NOT NULL,started INTEGER NOT NULL,completed INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,reserved_nano INTEGER NOT NULL,cost_upper_nano INTEGER,prompt_tokens INTEGER,cached_tokens INTEGER,completion_tokens INTEGER,http_status INTEGER NOT NULL DEFAULT 0,pricing TEXT NOT NULL,price_basis TEXT NOT NULL DEFAULT 'reserved_peak');
CREATE INDEX IF NOT EXISTS ai_calls_month ON ai_calls(month);
CREATE INDEX IF NOT EXISTS ai_calls_job ON ai_calls(job_id,id DESC);
CREATE TABLE IF NOT EXISTS ai_call_timings(call_id INTEGER PRIMARY KEY REFERENCES ai_calls(id),started_ms INTEGER NOT NULL,completed_ms INTEGER NOT NULL DEFAULT 0);
CREATE VIEW IF NOT EXISTS ai_budget_entries AS
SELECT 'probe' AS kind,id AS call_id,month,status,reserved_nano,cost_upper_nano FROM ai_probe_runs
UNION ALL SELECT 'analysis',CAST(id AS TEXT),month,status,reserved_nano,cost_upper_nano FROM ai_calls;
UPDATE ai_jobs SET status='uncertain',error='上次调用在进程退出前未完成；费用预留保留，未自动重试' WHERE status='running';
UPDATE ai_calls SET status='uncertain' WHERE status='running';
`)
	return err
}

type AISettings struct {
	Enabled   bool `json:"enabled"`
	Automatic bool `json:"automatic"`
}
type AIJob struct {
	ID            string       `json:"id"`
	Model         string       `json:"model"`
	PromptVersion string       `json:"prompt_version"`
	Evidence      []AIEvidence `json:"evidence"`
	Status        string       `json:"status"`
	Created       int64        `json:"created"`
	Completed     int64        `json:"completed"`
	Expires       int64        `json:"expires"`
	Result        *AIResult    `json:"result"`
	Error         string       `json:"error"`
	Attempts      int          `json:"attempts"`
	Stale         bool         `json:"stale"`
	Calls         []AICall     `json:"calls"`
}

type AICall struct {
	Started      int64  `json:"started"`
	Status       string `json:"status"`
	Reserved     int64  `json:"reserved_nano"`
	Estimated    *int64 `json:"estimated_nano"`
	Prompt       *int64 `json:"prompt_tokens"`
	Completion   *int64 `json:"completion_tokens"`
	PriceVersion string `json:"price_version"`
	PriceBasis   string `json:"price_basis"`
}

var ErrAIDisabled = errors.New("AI 尚未启用，请先检查解读设置和服务端配置")
var ErrAIQueue = errors.New("解读队列已满，请等待已有任务完成")

func (s *Store) SaveAISettings(ctx context.Context, v AISettings, now int64) error {
	// Changing automatic scope starts from the current watermark; never charges for historical backlog.
	_, err := s.DB.ExecContext(ctx, `UPDATE ai_settings SET cursor=CASE WHEN enabled<>? OR automatic<>? THEN (SELECT COALESCE(MAX(seq),0) FROM news_changes) ELSE cursor END,enabled_at=CASE WHEN enabled<>? OR automatic<>? THEN ? ELSE enabled_at END,enabled=?,automatic=?,not_before=0 WHERE singleton=1`, v.Enabled, v.Automatic, v.Enabled, v.Automatic, now, v.Enabled, v.Automatic)
	return err
}

func evidenceRow(row scanner, ref string) (AIEvidence, error) {
	var e AIEvidence
	e.ID = ref
	err := row.Scan(&e.NewsID, &e.Version, &e.Source, &e.URL, &e.Title, &e.Text, &e.Published)
	var cut bool
	e.Title, cut = boundedText(e.Title, 400)
	e.Text, e.Truncated = boundedText(e.Text, 5000)
	e.Truncated = e.Truncated || cut
	return e, err
}

const evidenceColumns = `n.id,n.version,n.source,n.url,n.title,n.content,n.published_at`

func evidenceChanged(q interface{ QueryRow(string, ...any) *sql.Row }, e AIEvidence) (bool, error) {
	current, err := evidenceRow(q.QueryRow(`SELECT `+evidenceColumns+` FROM news n WHERE n.id=?`, e.NewsID), e.ID)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return current.Title != e.Title || current.Text != e.Text || current.URL != e.URL || current.Published != e.Published, nil
}

func enqueueAnalysis(tx *sql.Tx, id int64, model string, retry bool, now int64) (string, error) {
	primary, err := evidenceRow(tx.QueryRow(`SELECT `+evidenceColumns+` FROM news n WHERE n.id=?`, id), "e1")
	if err != nil {
		return "", err
	}
	all := []AIEvidence{primary}
	// Only exact URL/content relations become input. Similar titles stay outside model evidence.
	rows, err := tx.Query(`SELECT `+evidenceColumns+` FROM news n JOIN news_features f ON f.news_id=n.id AND f.item_version=n.version WHERE n.id<>? AND n.source<>? AND ((n.url<>'' AND n.url=?) OR (f.content_key<>'' AND f.content_key=(SELECT content_key FROM news_features WHERE news_id=?) AND ABS(n.first_seen-(SELECT first_seen FROM news WHERE id=?))<=129600)) ORDER BY n.id LIMIT 2`, id, primary.Source, primary.URL, id, id)
	if err != nil {
		return "", err
	}
	for rows.Next() {
		e, eErr := evidenceRow(rows, fmt.Sprintf("e%d", len(all)+1))
		if eErr != nil {
			rows.Close()
			return "", eErr
		}
		all = append(all, e)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return "", err
	}
	// Cache exact bounded content, publication time and URL. Source identifiers/version counters
	// are excluded; reused results keep their ORIGINAL evidence snapshot and source attribution.
	signatures := []string{}
	for _, e := range all {
		b, _ := json.Marshal([]any{e.Title, e.Text, e.URL, e.Published})
		sig := string(b)
		found := false
		for _, s := range signatures {
			if s == sig {
				found = true
			}
		}
		if !found {
			signatures = append(signatures, sig)
		}
	}
	sort.Strings(signatures)
	b, _ := json.Marshal([]any{model, analysisPromptVersion, signatures})
	key := digest(string(b))
	if _, err = tx.Exec(`DELETE FROM ai_jobs WHERE id=? AND expires<=? AND status<>'running'`, key, now); err != nil {
		return "", err
	}
	var exists int
	if err = tx.QueryRow(`SELECT COUNT(*) FROM ai_jobs WHERE id=?`, key).Scan(&exists); err != nil {
		return "", err
	}
	if exists == 0 {
		var queued int
		if err = tx.QueryRow(`SELECT COUNT(*) FROM ai_jobs WHERE status IN ('queued','running','budget_wait')`).Scan(&queued); err != nil {
			return "", err
		}
		if queued >= 100 {
			return "", ErrAIQueue
		}
		payload, _ := json.Marshal(all)
		_, err = tx.Exec(`INSERT INTO ai_jobs(id,model,prompt_version,evidence,status,created,expires) VALUES(?,?,?,?, 'queued',?,?)`, key, model, analysisPromptVersion, string(payload), now, time.Unix(now, 0).AddDate(1, 0, 0).Unix())
		if err != nil {
			return "", err
		}
	} else if retry {
		_, err = tx.Exec(`UPDATE ai_jobs SET status='queued',error='' WHERE id=? AND status IN ('http_error','request_failed','invalid_output','usage_unknown','uncertain','reservation_exceeded') AND attempts<3`, key)
		if err != nil {
			return "", err
		}
	}
	if _, err = tx.Exec(`UPDATE ai_jobs SET status='queued',error='' WHERE id=? AND status IN ('superseded','config_changed') AND attempts<3`, key); err != nil {
		return "", err
	}
	_, err = tx.Exec(`INSERT INTO ai_links(news_id,version,job_id) VALUES(?,?,?) ON CONFLICT(news_id,version) DO UPDATE SET job_id=excluded.job_id`, id, primary.Version, key)
	return key, err
}

func (s *Store) QueueAnalysis(ctx context.Context, id int64, c AIConfig, retry bool, now int64) (string, error) {
	if !c.valid(time.Unix(now, 0)) {
		return "", ErrAIDisabled
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	var enabled bool
	if err = tx.QueryRow(`SELECT enabled FROM ai_settings WHERE singleton=1`).Scan(&enabled); err != nil {
		return "", err
	}
	if !enabled {
		return "", ErrAIDisabled
	}
	key, err := enqueueAnalysis(tx, id, c.Model, retry, now)
	if err != nil {
		return "", err
	}
	return key, tx.Commit()
}

func (s *Store) PlanAnalysis(ctx context.Context, c AIConfig, now int64) error {
	if !c.valid(time.Unix(now, 0)) {
		return nil
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var enabled, auto bool
	var cursor, since int64
	if err = tx.QueryRow(`SELECT enabled,automatic,cursor,enabled_at FROM ai_settings WHERE singleton=1`).Scan(&enabled, &auto, &cursor, &since); err != nil {
		return err
	}
	if !enabled || !auto {
		return nil
	}
	rows, err := tx.Query(`SELECT c.seq,COALESCE(n.id,0),COALESCE(f.importance,0),COALESCE(n.ingested_at,0),COALESCE(n.baseline,1) FROM news_changes c LEFT JOIN news n ON n.id=c.news_id LEFT JOIN news_features f ON f.news_id=n.id AND f.item_version=n.version WHERE c.seq>? ORDER BY c.seq LIMIT 25`, cursor)
	if err != nil {
		return err
	}
	type candidate struct {
		seq, id, importance, ingested int64
		baseline                      bool
	}
	list := []candidate{}
	for rows.Next() {
		var v candidate
		if err = rows.Scan(&v.seq, &v.id, &v.importance, &v.ingested, &v.baseline); err != nil {
			rows.Close()
			return err
		}
		list = append(list, v)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for _, v := range list {
		if v.id > 0 && v.importance >= 3 && !v.baseline && v.ingested >= since && v.ingested >= now-86400 {
			if _, err = enqueueAnalysis(tx, v.id, c.Model, false, now); errors.Is(err, ErrAIQueue) {
				break
			} else if err != nil {
				return err
			}
		}
		cursor = v.seq
	}
	_, err = tx.Exec(`UPDATE ai_settings SET cursor=? WHERE singleton=1`, cursor)
	if err != nil {
		return err
	}
	return tx.Commit()
}

func scanAIJob(row scanner) (AIJob, error) {
	var j AIJob
	var evidence, result string
	err := row.Scan(&j.ID, &j.Model, &j.PromptVersion, &evidence, &j.Status, &j.Created, &j.Completed, &j.Expires, &result, &j.Error, &j.Attempts)
	if err == nil {
		err = json.Unmarshal([]byte(evidence), &j.Evidence)
	}
	if err == nil && result != "" {
		err = json.Unmarshal([]byte(result), &j.Result)
	}
	return j, err
}

const aiJobColumns = `id,model,prompt_version,evidence,status,created,completed,expires,result,error,attempts`

func (s *Store) Analysis(ctx context.Context, id string) (AIJob, error) {
	j, err := scanAIJob(s.DB.QueryRowContext(ctx, `SELECT `+aiJobColumns+` FROM ai_jobs WHERE id=? AND expires>?`, id, time.Now().Unix()))
	if err != nil {
		return j, err
	}
	j.Calls = []AICall{}
	rows, err := s.DB.QueryContext(ctx, `SELECT started,status,reserved_nano,cost_upper_nano,prompt_tokens,completion_tokens,COALESCE(json_extract(pricing,'$.peak_pricing.version'),''),price_basis FROM ai_calls WHERE job_id=? ORDER BY id DESC LIMIT 10`, id)
	if err != nil {
		return j, err
	}
	for rows.Next() {
		var c AICall
		if err = rows.Scan(&c.Started, &c.Status, &c.Reserved, &c.Estimated, &c.Prompt, &c.Completion, &c.PriceVersion, &c.PriceBasis); err != nil {
			rows.Close()
			return j, err
		}
		j.Calls = append(j.Calls, c)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return j, err
	}
	for _, e := range j.Evidence {
		changed, err := evidenceChanged(s.DB, e)
		if err != nil {
			return j, err
		}
		j.Stale = j.Stale || changed
	}
	return j, nil
}

func (s *Store) NewsAnalysis(ctx context.Context, id int64) (*AIJob, error) {
	var key string
	var version, current int
	err := s.DB.QueryRowContext(ctx, `SELECT l.job_id,l.version,n.version FROM ai_links l JOIN news n ON n.id=l.news_id WHERE n.id=? ORDER BY l.version DESC LIMIT 1`, id).Scan(&key, &version, &current)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	j, err := s.Analysis(ctx, key)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	j.Stale = j.Stale || version != current
	return &j, nil
}

func (a *App) AIOverview(ctx context.Context) (map[string]any, error) {
	var settings AISettings
	var pause int64
	if err := a.Store.DB.QueryRowContext(ctx, `SELECT enabled,automatic,not_before FROM ai_settings WHERE singleton=1`).Scan(&settings.Enabled, &settings.Automatic, &pause); err != nil {
		return nil, err
	}
	zone, _ := time.LoadLocation("Asia/Shanghai")
	month := time.Now().In(zone).Format("2006-01")
	var known, reserved, probe int64
	err := a.Store.DB.QueryRowContext(ctx, `SELECT COALESCE(SUM(cost_upper_nano),0),COALESCE(SUM(CASE WHEN cost_upper_nano IS NULL THEN reserved_nano ELSE 0 END),0),COALESCE(SUM(CASE WHEN kind='probe' THEN COALESCE(cost_upper_nano,reserved_nano) ELSE 0 END),0) FROM ai_budget_entries WHERE month=?`, month).Scan(&known, &reserved, &probe)
	if err != nil {
		return nil, err
	}
	rows, err := a.Store.DB.QueryContext(ctx, `SELECT id,status,created,error,attempts,json_extract(evidence,'$[0].title') FROM ai_jobs ORDER BY created DESC,id DESC LIMIT 20`)
	if err != nil {
		return nil, err
	}
	recent := []map[string]any{}
	for rows.Next() {
		var id, status, message, title string
		var created int64
		var attempts int
		if err = rows.Scan(&id, &status, &created, &message, &attempts, &title); err != nil {
			rows.Close()
			return nil, err
		}
		recent = append(recent, map[string]any{"id": id, "status": status, "created": created, "error": message, "attempts": attempts, "title": title})
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	var queued int
	if err = a.Store.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM ai_jobs WHERE status IN ('queued','running','budget_wait')`).Scan(&queued); err != nil {
		return nil, err
	}
	return map[string]any{"settings": settings, "configured": a.AIConfig.valid(time.Now()), "model": a.AIConfig.Model, "price_version": a.AIConfig.Pricing.Version, "prices_valid_until": a.AIConfig.ValidUntil, "month": month, "budget_nano": monthlyBudgetNano, "estimated_nano": known, "reserved_nano": reserved, "probe_nano": probe, "remaining_nano": max(int64(0), monthlyBudgetNano-known-reserved), "queued": queued, "not_before": pause, "recent": recent}, nil
}
