package probe

import (
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"errors"
	"slices"
	"strings"
	"time"

	topics "finevent/internal/news"
)

func initNews(db *sql.DB) error {
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for table, columns := range map[string]map[string]string{
		"news": {"ingested_at": "INTEGER NOT NULL DEFAULT 0", "updated_at": "INTEGER NOT NULL DEFAULT 0"},
	} {
		rows, e := tx.Query("PRAGMA table_info(" + table + ")")
		if e != nil {
			return e
		}
		present := map[string]bool{}
		for rows.Next() {
			var id, notnull, pk int
			var name, kind string
			var def any
			if e = rows.Scan(&id, &name, &kind, &notnull, &def, &pk); e != nil {
				rows.Close()
				return e
			}
			present[name] = true
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return e
		}
		for name, kind := range columns {
			if !present[name] {
				if _, err = tx.Exec("ALTER TABLE " + table + " ADD COLUMN " + name + " " + kind); err != nil {
					return err
				}
			}
		}
	}
	// Keep the five-column revisions table compatible with the previous release's positional INSERT.
	_, err = tx.Exec(`CREATE TABLE IF NOT EXISTS news_identity(singleton INTEGER PRIMARY KEY CHECK(singleton=1),last_id INTEGER NOT NULL);
INSERT OR IGNORE INTO news_identity VALUES(1,0);
UPDATE news_identity SET last_id=MAX(last_id,COALESCE((SELECT MAX(id) FROM news),0)) WHERE singleton=1;
CREATE TRIGGER IF NOT EXISTS news_stable_identity AFTER INSERT ON news BEGIN
  UPDATE news SET id=(SELECT last_id+1 FROM news_identity WHERE singleton=1) WHERE id=NEW.id AND NEW.id<=(SELECT last_id FROM news_identity WHERE singleton=1);
  UPDATE news_identity SET last_id=MAX(last_id,(SELECT MAX(id) FROM news)) WHERE singleton=1;
END;
CREATE TABLE IF NOT EXISTS news_revision_evidence(news_id INTEGER NOT NULL,version INTEGER NOT NULL,url TEXT NOT NULL,published_at INTEGER NOT NULL,PRIMARY KEY(news_id,version),FOREIGN KEY(news_id,version) REFERENCES news_revisions(news_id,version) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS news_features(news_id INTEGER PRIMARY KEY REFERENCES news(id) ON DELETE CASCADE,item_version INTEGER NOT NULL,rule_version TEXT NOT NULL,payload TEXT NOT NULL,importance INTEGER NOT NULL,content_key TEXT NOT NULL,indexed_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS news_labels(news_id INTEGER NOT NULL REFERENCES news(id) ON DELETE CASCADE,kind TEXT NOT NULL,code TEXT NOT NULL,PRIMARY KEY(news_id,kind,code));
CREATE INDEX IF NOT EXISTS news_labels_filter ON news_labels(kind,code,news_id);
CREATE TABLE IF NOT EXISTS news_processing(news_id INTEGER PRIMARY KEY REFERENCES news(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS news_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,news_id INTEGER REFERENCES news(id) ON DELETE SET NULL,changed_at INTEGER NOT NULL);
CREATE TRIGGER IF NOT EXISTS news_deleted AFTER DELETE ON news BEGIN INSERT INTO news_changes(news_id,changed_at) VALUES(NULL,unixepoch()); END;
CREATE INDEX IF NOT EXISTS news_changes_item ON news_changes(news_id);
CREATE INDEX IF NOT EXISTS news_discovered ON news(first_seen DESC,id DESC);
CREATE INDEX IF NOT EXISTS news_published ON news(published_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS news_url ON news(url);
CREATE INDEX IF NOT EXISTS news_content ON news_features(content_key);
CREATE INDEX IF NOT EXISTS news_features_importance ON news_features(importance,news_id);`)
	if err != nil {
		return err
	}
	_, err = tx.Exec(`INSERT OR IGNORE INTO news_processing SELECT n.id FROM news n LEFT JOIN news_features f ON f.news_id=n.id WHERE f.news_id IS NULL OR f.item_version<>n.version OR f.rule_version<>?`, topics.Version)
	if err != nil {
		return err
	}
	return tx.Commit()
}

func indexNews(tx *sql.Tx, id, now int64) error {
	var title, body string
	var version int
	if err := tx.QueryRow(`SELECT title,content,version FROM news WHERE id=?`, id).Scan(&title, &body, &version); err != nil {
		return err
	}
	r := topics.Classify(title, body)
	payload, err := json.Marshal(r)
	if err != nil {
		return err
	}
	if _, err = tx.Exec(`INSERT OR REPLACE INTO news_features VALUES(?,?,?,?,?,?,?)`, id, version, topics.Version, string(payload), r.Importance, topics.Fingerprint(body), now); err != nil {
		return err
	}
	if _, err = tx.Exec(`DELETE FROM news_labels WHERE news_id=?`, id); err != nil {
		return err
	}
	for kind, values := range map[string][]string{"category": r.Categories, "industry": r.Industries} {
		for _, code := range values {
			if _, err = tx.Exec(`INSERT INTO news_labels VALUES(?,?,?)`, id, kind, code); err != nil {
				return err
			}
		}
	}
	if _, err = tx.Exec(`INSERT INTO news_changes(news_id,changed_at) VALUES(?,?)`, id, now); err != nil {
		return err
	}
	_, err = tx.Exec(`DELETE FROM news_processing WHERE news_id=?`, id)
	return err
}

func (s *Store) ProcessNewsBatch(ctx context.Context, now int64) (int, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	rows, err := tx.Query(`SELECT news_id FROM news_processing ORDER BY news_id DESC LIMIT 25`)
	if err != nil {
		return 0, err
	}
	ids := []int64{}
	for rows.Next() {
		var id int64
		if err = rows.Scan(&id); err != nil {
			rows.Close()
			return 0, err
		}
		ids = append(ids, id)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return 0, err
	}
	for _, id := range ids {
		// An older rollback binary does not maintain the new modification timestamp.
		if _, err = tx.Exec(`UPDATE news SET updated_at=0 WHERE id=? AND EXISTS(SELECT 1 FROM news_features f WHERE f.news_id=news.id AND f.item_version<>news.version)`, id); err != nil {
			return 0, err
		}
		if err = indexNews(tx, id, now); err != nil {
			return 0, err
		}
	}
	return len(ids), tx.Commit()
}

type NewsEntry struct {
	Item
	IngestedAt     int64          `json:"ingested_at"`
	UpdatedAt      int64          `json:"updated_at"`
	Classification *topics.Result `json:"classification"`
}

const newsColumns = `n.id,n.source,n.source_id,n.title,n.content,n.url,n.published_at,n.first_seen,n.last_seen,n.version,n.baseline,n.source_observed_at,n.ingested_at,n.updated_at,CASE WHEN f.item_version=n.version AND f.rule_version='` + topics.Version + `' THEN f.payload ELSE '' END`

var newsListColumns = strings.Replace(newsColumns, "n.content,", "'',", 1)

type scanner interface{ Scan(...any) error }

func scanNews(row scanner) (NewsEntry, error) {
	var n NewsEntry
	var payload string
	err := row.Scan(&n.ID, &n.Source, &n.SourceID, &n.Title, &n.Content, &n.URL, &n.PublishedAt, &n.FirstSeen, &n.LastSeen, &n.Version, &n.Baseline, &n.SourceObservedAt, &n.IngestedAt, &n.UpdatedAt, &payload)
	if err == nil && payload != "" {
		err = json.Unmarshal([]byte(payload), &n.Classification)
	}
	return n, err
}

type NewsFilter struct {
	Source, Category, Industry, Query, Order, Cursor string
	Since                                            int64
	Importance                                       int
}
type newsCursor struct {
	Time, ID, MaxID int64
	Filter          string
}
type NewsPage struct {
	Items     []NewsEntry `json:"items"`
	Next      string      `json:"next"`
	Watermark int64       `json:"watermark"`
	Backlog   int         `json:"backlog"`
}

var ErrNewsFilter = errors.New("新闻筛选或分页参数无效，请刷新列表")

func validLabel(code string, labels []topics.Label) bool {
	return code == "" || slices.ContainsFunc(labels, func(l topics.Label) bool { return l.ID == code })
}
func (s *Store) NewsFeed(ctx context.Context, q NewsFilter) (NewsPage, error) {
	out := NewsPage{Items: []NewsEntry{}}
	if (q.Order != "discovered" && q.Order != "published") || q.Since < 0 || q.Importance < 0 || q.Importance > 3 || len([]rune(q.Query)) > 100 || len(q.Source) > 100 || (q.Category != "unclassified" && !validLabel(q.Category, topics.Categories())) || !validLabel(q.Industry, topics.Industries()) {
		return out, ErrNewsFilter
	}
	filterJSON, _ := json.Marshal([]any{q.Source, q.Category, q.Industry, q.Query, q.Order, q.Since, q.Importance})
	key := digest(string(filterJSON))
	cursor := newsCursor{Filter: key}
	if q.Cursor != "" {
		raw, e := base64.RawURLEncoding.DecodeString(q.Cursor)
		if e != nil || len(raw) > 500 {
			return out, ErrNewsFilter
		}
		if json.Unmarshal(raw, &cursor) != nil || cursor.Filter != key || cursor.ID <= 0 || cursor.MaxID < cursor.ID || cursor.Time < 0 {
			return out, ErrNewsFilter
		}
	}
	tx, err := s.DB.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return out, err
	}
	defer tx.Rollback()
	if err = tx.QueryRow(`SELECT COALESCE(MAX(seq),0) FROM news_changes`).Scan(&out.Watermark); err != nil {
		return out, err
	}
	if err = tx.QueryRow(`SELECT COUNT(*) FROM news_processing`).Scan(&out.Backlog); err != nil {
		return out, err
	}
	if q.Cursor == "" {
		if err = tx.QueryRow(`SELECT COALESCE(MAX(id),0) FROM news`).Scan(&cursor.MaxID); err != nil {
			return out, err
		}
	}
	order := "n.first_seen"
	if q.Order == "published" {
		order = "n.published_at"
	}
	where := `n.id<=? AND n.first_seen>=?`
	args := []any{cursor.MaxID, q.Since}
	if q.Source != "" {
		where += ` AND n.source=?`
		args = append(args, q.Source)
	}
	if q.Query != "" {
		where += ` AND (instr(lower(n.title),lower(?))>0 OR instr(lower(n.content),lower(?))>0)`
		args = append(args, q.Query, q.Query)
	}
	if q.Category != "" || q.Industry != "" || q.Importance > 0 {
		where += ` AND f.item_version=n.version AND f.rule_version=?`
		args = append(args, topics.Version)
	}
	for kind, code := range map[string]string{"category": q.Category, "industry": q.Industry} {
		if code == "unclassified" {
			where += ` AND NOT EXISTS(SELECT 1 FROM news_labels l WHERE l.news_id=n.id AND l.kind='category')`
		} else if code != "" {
			where += ` AND EXISTS(SELECT 1 FROM news_labels l WHERE l.news_id=n.id AND l.kind=? AND l.code=?)`
			args = append(args, kind, code)
		}
	}
	if q.Importance > 0 {
		where += ` AND f.importance>=?`
		args = append(args, q.Importance)
	}
	if q.Cursor != "" {
		where += ` AND (` + order + `<? OR (` + order + `=? AND n.id<?))`
		args = append(args, cursor.Time, cursor.Time, cursor.ID)
	}
	rows, err := tx.QueryContext(ctx, `SELECT `+newsListColumns+` FROM news n LEFT JOIN news_features f ON f.news_id=n.id WHERE `+where+` ORDER BY `+order+` DESC,n.id DESC LIMIT 31`, args...)
	if err != nil {
		return out, err
	}
	for rows.Next() {
		n, e := scanNews(rows)
		if e != nil {
			rows.Close()
			return out, e
		}
		n.Content = ""
		out.Items = append(out.Items, n)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return out, err
	}
	if len(out.Items) > 30 {
		out.Items = out.Items[:30]
		last := out.Items[29]
		cursor.ID = last.ID
		cursor.Time = last.FirstSeen
		if q.Order == "published" {
			cursor.Time = last.PublishedAt
		}
		raw, _ := json.Marshal(cursor)
		out.Next = base64.RawURLEncoding.EncodeToString(raw)
	}
	return out, tx.Commit()
}

type NewsRevision struct {
	Version     int     `json:"version"`
	Title       string  `json:"title"`
	Content     string  `json:"content"`
	URL         *string `json:"url"`
	PublishedAt *int64  `json:"published_at"`
	SavedAt     int64   `json:"saved_at"`
}
type NewsRelation struct {
	Item NewsEntry `json:"item"`
	Kind string    `json:"kind"`
}
type NewsDetail struct {
	Item          NewsEntry      `json:"item"`
	Revisions     []NewsRevision `json:"revisions"`
	Related       []NewsRelation `json:"related"`
	RevisionMore  bool           `json:"revision_more"`
	RelationScope string         `json:"relation_scope"`
}

func (s *Store) NewsDetail(ctx context.Context, id int64) (NewsDetail, error) {
	out := NewsDetail{Revisions: []NewsRevision{}, Related: []NewsRelation{}, RelationScope: "相同链接/正文最多列出30条；相似标题仅检查首次发现相差72小时内的最近200条跨源报道，需人工核对，未合并或删除原文。"}
	tx, err := s.DB.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return out, err
	}
	defer tx.Rollback()
	out.Item, err = scanNews(tx.QueryRowContext(ctx, `SELECT `+newsColumns+` FROM news n LEFT JOIN news_features f ON f.news_id=n.id WHERE n.id=?`, id))
	if err != nil {
		return out, err
	}
	rows, err := tx.QueryContext(ctx, `SELECT r.version,r.title,r.content,e.url,e.published_at,r.saved_at FROM news_revisions r LEFT JOIN news_revision_evidence e ON e.news_id=r.news_id AND e.version=r.version WHERE r.news_id=? ORDER BY r.version DESC LIMIT 31`, id)
	if err != nil {
		return out, err
	}
	for rows.Next() {
		var r NewsRevision
		if err = rows.Scan(&r.Version, &r.Title, &r.Content, &r.URL, &r.PublishedAt, &r.SavedAt); err != nil {
			rows.Close()
			return out, err
		}
		out.Revisions = append(out.Revisions, r)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return out, err
	}
	if len(out.Revisions) > 30 {
		out.Revisions = out.Revisions[:30]
		out.RevisionMore = true
	}
	n := out.Item
	key := topics.Fingerprint(n.Content)
	rows, err = tx.QueryContext(ctx, `SELECT `+newsListColumns+` FROM news n LEFT JOIN news_features f ON f.news_id=n.id WHERE n.source<>? AND ((n.url<>'' AND n.url=?) OR (f.item_version=n.version AND f.content_key<>'' AND f.content_key=? AND ABS(n.first_seen-?)<=129600)) ORDER BY n.first_seen DESC,n.id DESC LIMIT 30`, n.Source, n.URL, key, n.FirstSeen)
	if err != nil {
		return out, err
	}
	seen := map[int64]bool{}
	for rows.Next() {
		other, e := scanNews(rows)
		if e != nil {
			rows.Close()
			return out, e
		}
		kind := "same_content"
		if other.URL == n.URL && n.URL != "" {
			kind = "same_url"
		}
		other.Content = ""
		out.Related = append(out.Related, NewsRelation{other, kind})
		seen[other.ID] = true
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return out, err
	}
	rows, err = tx.QueryContext(ctx, `SELECT id,substr(title,1,300) FROM news WHERE source<>? AND first_seen BETWEEN ? AND ? ORDER BY first_seen DESC,id DESC LIMIT 200`, n.Source, n.FirstSeen-3*86400, n.FirstSeen+3*86400)
	if err != nil {
		return out, err
	}
	ids := []int64{}
	title := string([]rune(n.Title)[:min(300, len([]rune(n.Title)))])
	for rows.Next() {
		var otherID int64
		var otherTitle string
		if err = rows.Scan(&otherID, &otherTitle); err != nil {
			rows.Close()
			return out, err
		}
		if !seen[otherID] && len(ids) < 10 && topics.Similar(title, otherTitle) {
			ids = append(ids, otherID)
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return out, err
	}
	for _, otherID := range ids {
		other, e := scanNews(tx.QueryRowContext(ctx, `SELECT `+newsListColumns+` FROM news n LEFT JOIN news_features f ON f.news_id=n.id WHERE n.id=?`, otherID))
		if e != nil {
			return out, e
		}
		other.Content = ""
		out.Related = append(out.Related, NewsRelation{other, "similar_title"})
	}
	return out, tx.Commit()
}

type NewsChannel struct {
	ID          string `json:"id"`
	LastAttempt int64  `json:"last_attempt"`
	LastSuccess int64  `json:"last_success"`
	Success     bool   `json:"success"`
	Error       string `json:"error"`
	Gap         string `json:"gap"`
	GapAt       int64  `json:"gap_at"`
}

func (s *Store) NewsMeta(ctx context.Context) (map[string]any, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT DISTINCT source FROM news ORDER BY source LIMIT 100`)
	if err != nil {
		return nil, err
	}
	sources := []string{}
	for rows.Next() {
		var source string
		if err = rows.Scan(&source); err != nil {
			rows.Close()
			return nil, err
		}
		sources = append(sources, source)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	channels := []NewsChannel{}
	for _, id := range []string{"wallstreetcn", "trendradar"} {
		c := NewsChannel{ID: id}
		err = s.DB.QueryRowContext(ctx, `SELECT completed,success,error FROM polls WHERE source=? ORDER BY id DESC LIMIT 1`, id).Scan(&c.LastAttempt, &c.Success, &c.Error)
		if err != nil && err != sql.ErrNoRows {
			return nil, err
		}
		if err = s.DB.QueryRowContext(ctx, `SELECT COALESCE(MAX(completed),0) FROM polls WHERE source=? AND success=1`, id).Scan(&c.LastSuccess); err != nil {
			return nil, err
		}
		err = s.DB.QueryRowContext(ctx, `SELECT completed,gap FROM polls WHERE source=? AND gap<>'' ORDER BY id DESC LIMIT 1`, id).Scan(&c.GapAt, &c.Gap)
		if err != nil && err != sql.ErrNoRows {
			return nil, err
		}
		channels = append(channels, c)
	}
	return map[string]any{"categories": topics.Categories(), "industries": topics.Industries(), "sources": sources, "channels": channels, "rule_version": topics.Version, "server_time": time.Now().Unix()}, nil
}

func (s *Store) NewsChanges(ctx context.Context, after int64) (map[string]any, error) {
	var first, last, count int64
	err := s.DB.QueryRowContext(ctx, `SELECT COALESCE(MIN(seq),0),COALESCE(MAX(seq),0),COUNT(DISTINCT CASE WHEN seq>? THEN COALESCE(news_id,-seq) END) FROM news_changes`, after).Scan(&first, &last, &count)
	return map[string]any{"watermark": last, "changed": count, "reset": after > last || after > 0 && first > after+1}, err
}

func trimNewsQuery(s string) string { return strings.TrimSpace(s) }
