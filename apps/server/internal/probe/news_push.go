package probe

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"slices"
	"strings"
	"time"

	topics "finevent/internal/news"
)

// News alerts deliberately use source facts and existing rules, without a model call.
type NewsPushSettings struct {
	Enabled    bool     `json:"enabled"`
	Devices    []int64  `json:"devices"`
	Categories []string `json:"categories"`
	Industries []string `json:"industries"`
}

func initNewsPush(db *sql.DB) error {
	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS news_push_settings(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL,cursor INTEGER NOT NULL,enabled_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS news_push_events(id INTEGER PRIMARY KEY AUTOINCREMENT,created INTEGER NOT NULL,material TEXT NOT NULL,revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS news_push_aliases(key TEXT PRIMARY KEY,event_id INTEGER NOT NULL REFERENCES news_push_events(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS news_push_alias_event ON news_push_aliases(event_id);
CREATE TABLE IF NOT EXISTS news_push_links(news_id INTEGER PRIMARY KEY REFERENCES news(id) ON DELETE CASCADE,event_id INTEGER NOT NULL REFERENCES news_push_events(id) ON DELETE CASCADE,version INTEGER NOT NULL,material TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_push_jobs(key TEXT PRIMARY KEY,delivery_id INTEGER UNIQUE NOT NULL REFERENCES deliveries(id) ON DELETE CASCADE,event_id INTEGER REFERENCES news_push_events(id) ON DELETE SET NULL,news_id INTEGER REFERENCES news(id) ON DELETE SET NULL,version INTEGER NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,created INTEGER NOT NULL,reason TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS news_push_job_news ON news_push_jobs(news_id,version);
CREATE INDEX IF NOT EXISTS news_push_job_event ON news_push_jobs(event_id);`)
	return err
}

func newsPushSettings(db reminderReader) (NewsPushSettings, int64, int64, error) {
	p := NewsPushSettings{Devices: []int64{}, Categories: []string{}, Industries: []string{}}
	var raw string
	var cursor, enabledAt int64
	err := db.QueryRow(`SELECT payload,cursor,enabled_at FROM news_push_settings WHERE id=1`).Scan(&raw, &cursor, &enabledAt)
	if err == sql.ErrNoRows {
		return p, 0, 0, nil
	}
	if err == nil {
		err = json.Unmarshal([]byte(raw), &p)
	}
	return p, cursor, enabledAt, err
}

func (p *NewsPushSettings) normalize() {
	if p.Devices == nil {
		p.Devices = []int64{}
	}
	if p.Categories == nil {
		p.Categories = []string{}
	}
	if p.Industries == nil {
		p.Industries = []string{}
	}
	slices.Sort(p.Devices)
	p.Devices = slices.Compact(p.Devices)
	slices.Sort(p.Categories)
	p.Categories = slices.Compact(p.Categories)
	slices.Sort(p.Industries)
	p.Industries = slices.Compact(p.Industries)
}

func (p NewsPushSettings) validate(db reminderReader) error {
	if len(p.Devices) > 20 || len(p.Categories) > 10 || len(p.Industries) > 12 {
		return errors.New("关注或设备数量超出范围")
	}
	for _, pair := range []struct {
		values  []string
		allowed []topics.Label
	}{{p.Categories, topics.Categories()}, {p.Industries, topics.Industries()}} {
		for _, v := range pair.values {
			if !slices.ContainsFunc(pair.allowed, func(l topics.Label) bool { return l.ID == v }) {
				return errors.New("未知新闻关注标签")
			}
		}
	}
	for _, id := range p.Devices {
		var active int
		if db.QueryRow(`SELECT active FROM subscriptions WHERE id=?`, id).Scan(&active) != nil || (p.Enabled && active != 1) {
			return errors.New("请重新选择有效接收设备")
		}
	}
	if p.Enabled && (len(p.Devices) == 0 || len(p.Categories)+len(p.Industries) == 0) {
		return errors.New("开启新闻推送前，请选择接收设备和关注范围")
	}
	return nil
}

func (p NewsPushSettings) matches(r topics.Result) bool {
	return p.Enabled && r.Importance == 3 && (slices.ContainsFunc(r.Categories, func(v string) bool { return slices.Contains(p.Categories, v) }) || slices.ContainsFunc(r.Industries, func(v string) bool { return slices.Contains(p.Industries, v) }))
}

// Exact numbers and state words are a conservative signal, not a semantic verdict.
// A stylistic rewrite alone never produces another alert; similar titles aren't merged.
// TODO: This conservative rule can miss semantic changes without number/state words;
// extend only after reviewed examples and dedup regression coverage are available.
var newsPushNumbers = regexp.MustCompile(`[+-]?[0-9]+(?:\.[0-9]+)?(?:%|个百分点|亿元|万元|万亿|基点)?`)

func newsMaterial(title, body string) string {
	text := topics.Normalize(title + "\n" + body)
	parts := newsPushNumbers.FindAllString(text, -1)
	for _, word := range []string{"降准", "降息", "加息", "上调", "下调", "增长", "下降", "上涨", "下跌", "否认", "澄清", "取消", "暂停", "恢复", "批准", "终止", "生效", "未", "不"} {
		if strings.Contains(text, word) {
			parts = append(parts, word)
		}
	}
	slices.Sort(parts)
	return digest(strings.Join(slices.Compact(parts), "|"))
}

type newsPushItem struct {
	id, ingested, updated, published, seen  int64
	version                                 int
	baseline                                bool
	title, content, url, source, contentKey string
	rule                                    topics.Result
}

func readNewsPushItem(db reminderReader, id int64) (newsPushItem, error) {
	n := newsPushItem{id: id}
	var raw string
	err := db.QueryRow(`SELECT n.title,n.content,n.url,n.source,n.version,n.baseline,n.ingested_at,n.updated_at,n.published_at,n.first_seen,f.payload,f.content_key FROM news n JOIN news_features f ON f.news_id=n.id AND f.item_version=n.version WHERE n.id=?`, id).Scan(&n.title, &n.content, &n.url, &n.source, &n.version, &n.baseline, &n.ingested, &n.updated, &n.published, &n.seen, &raw, &n.contentKey)
	if err == nil {
		err = json.Unmarshal([]byte(raw), &n.rule)
	}
	return n, err
}

func (n newsPushItem) body(reason string) string {
	stamp := "来源发布时间未知"
	if n.published > 0 {
		stamp = "来源发布于 " + time.Unix(n.published, 0).In(time.FixedZone("CST", 8*3600)).Format("01/02 15:04")
	}
	return reason + " · " + n.source + " · " + stamp + "。点击核对原文；重要性为规则判断。"
}

func (a *App) PlanNewsPush(now int64) error {
	tx, err := a.Store.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	p, cursor, enabledAt, err := newsPushSettings(tx)
	if err != nil {
		return err
	}
	if !p.Enabled {
		return nil
	}
	rows, err := tx.Query(`SELECT seq,COALESCE(news_id,0) FROM news_changes WHERE seq>? ORDER BY seq LIMIT 100`, cursor)
	if err != nil {
		return err
	}
	type change struct{ seq, id int64 }
	changes := []change{}
	for rows.Next() {
		var c change
		if err = rows.Scan(&c.seq, &c.id); err != nil {
			break
		}
		changes = append(changes, c)
	}
	rowErr := rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	if rowErr != nil {
		return rowErr
	}
	for _, c := range changes {
		if c.id != 0 {
			if err = planNewsItem(tx, p, c.id, enabledAt, now); err != nil {
				return err
			}
		}
		cursor = c.seq
	}
	if _, err = tx.Exec(`UPDATE news_push_settings SET cursor=? WHERE id=1`, cursor); err != nil {
		return err
	}
	return tx.Commit()
}

func planNewsItem(tx *sql.Tx, p NewsPushSettings, id, enabledAt, now int64) error {
	n, err := readNewsPushItem(tx, id)
	if err == sql.ErrNoRows {
		return nil
	}
	if err != nil {
		return err
	}
	var group, created int64
	var previousVersion, eventRevision int
	var material, eventMaterial string
	err = tx.QueryRow(`SELECT e.id,e.created,l.material,l.version,e.material,e.revision FROM news_push_links l JOIN news_push_events e ON e.id=l.event_id WHERE l.news_id=?`, id).Scan(&group, &created, &material, &previousVersion, &eventMaterial, &eventRevision)
	if err != nil && err != sql.ErrNoRows {
		return err
	}
	linked := err == nil
	if linked && (n.version <= previousVersion || created < now-86400) {
		return nil
	}
	// Every pending send is rechecked too. A downgraded or obsolete item cannot leak out.
	if n.baseline || !p.matches(n.rule) || (linked && material != newsMaterial(n.title, n.content)) {
		if _, err = tx.Exec(`UPDATE deliveries SET status='cancelled',error='报道已修订，重新核对推送条件',receipt_token='' WHERE scenario='news' AND status='queued' AND id IN (SELECT delivery_id FROM news_push_jobs WHERE news_id=? AND version<>?)`, id, n.version); err != nil {
			return err
		}
	}
	if n.baseline || !p.matches(n.rule) || n.updated < enabledAt || n.updated < now-1800 || n.updated > now+300 {
		return nil
	}
	if !linked && (n.ingested < enabledAt || n.ingested < now-1800 || n.seen < enabledAt || (n.published > 0 && (n.published < now-1800 || n.published > now+300))) {
		return nil
	}
	keys := []string{}
	if u, e := url.Parse(n.url); e == nil && n.url != "" && (strings.Trim(u.Path, "/") != "" || u.RawQuery != "") {
		keys = append(keys, "url:"+n.url)
	}
	if n.contentKey != "" {
		keys = append(keys, "body:"+n.contentKey+":"+newsMaterial(n.title, n.content))
	}
	reason := "关注范围内的重要新闻"
	changed := false
	if !linked {
		for _, key := range keys {
			err = tx.QueryRow(`SELECT e.id,e.created,e.material,e.revision FROM news_push_aliases a JOIN news_push_events e ON e.id=a.event_id WHERE a.key=? AND e.created>=?`, key, now-86400).Scan(&group, &created, &eventMaterial, &eventRevision)
			if err == nil {
				if strings.HasPrefix(key, "body:") && eventMaterial != newsMaterial(n.title, n.content) {
					group = 0
					continue
				}
				break
			}
			if err != sql.ErrNoRows {
				return err
			}
		}
		if group == 0 {
			result, e := tx.Exec(`INSERT INTO news_push_events(created,material,revision) VALUES(?,?,1)`, now, newsMaterial(n.title, n.content))
			if e != nil {
				return e
			}
			eventRevision = 1
			group, err = result.LastInsertId()
			if err != nil {
				return err
			}
			changed = true
		}
	} else if material != newsMaterial(n.title, n.content) && eventMaterial != newsMaterial(n.title, n.content) {
		eventRevision++
		changed = true
		reason = "重要修订：关键数字或状态词变化"
	}
	if _, err = tx.Exec(`INSERT INTO news_push_links VALUES(?,?,?,?) ON CONFLICT(news_id) DO UPDATE SET version=excluded.version,material=excluded.material`, id, group, n.version, newsMaterial(n.title, n.content)); err != nil {
		return err
	}
	for _, key := range keys {
		if _, err = tx.Exec(`INSERT INTO news_push_aliases VALUES(?,?) ON CONFLICT(key) DO UPDATE SET event_id=excluded.event_id`, key, group); err != nil {
			return err
		}
	}
	if !changed {
		// Preserve a pending alert across a cosmetic edit, with the latest text and version.
		if linked {
			_, err = tx.Exec(`UPDATE news_push_jobs SET version=?,title=?,body=? WHERE news_id=? AND delivery_id IN (SELECT id FROM deliveries WHERE status='queued' AND expires>?)`, n.version, n.title, n.body("关注范围内的重要新闻"), id, now)
		}
		return err
	}
	if _, err = tx.Exec(`UPDATE deliveries SET status='cancelled',error='被重要修订替代',receipt_token='' WHERE status='queued' AND id IN (SELECT delivery_id FROM news_push_jobs WHERE event_id=?)`, group); err != nil {
		return err
	}
	signature := newsMaterial(n.title, n.content)
	if _, err = tx.Exec(`UPDATE news_push_events SET material=?,revision=? WHERE id=?`, signature, eventRevision, group); err != nil {
		return err
	}
	for _, device := range p.Devices {
		var active int
		if err = tx.QueryRow(`SELECT active FROM subscriptions WHERE id=?`, device).Scan(&active); err == sql.ErrNoRows {
			continue
		}
		if err != nil {
			return err
		}
		if active != 1 {
			continue
		}
		key := digest(fmt.Sprintf("news:%d:%d:%d", group, eventRevision, device))
		var exists int
		if err = tx.QueryRow(`SELECT COUNT(*) FROM news_push_jobs WHERE key=?`, key).Scan(&exists); err != nil {
			return err
		}
		if exists > 0 {
			continue
		}
		t := token()
		result, e := tx.Exec(`INSERT INTO deliveries(subscription_id,scenario,status,due,expires,receipt_hash,receipt_token) VALUES(?,'news','queued',?,?,?,?)`, device, now, now+900, digest(t), t)
		if e != nil {
			return e
		}
		delivery, e := result.LastInsertId()
		if e != nil {
			return e
		}
		if _, err = tx.Exec(`INSERT INTO news_push_jobs VALUES(?,?,?,?,?,?,?,?,?)`, key, delivery, group, id, n.version, n.title, n.body(reason), now, reason); err != nil {
			return err
		}
	}
	return nil
}

func validNewsDelivery(tx *sql.Tx, d *claimedDelivery, now int64) (bool, error) {
	p, _, _, err := newsPushSettings(tx)
	if err != nil {
		return false, err
	}
	if !p.Enabled || !slices.Contains(p.Devices, d.SubscriptionID) {
		return false, nil
	}
	var version int
	err = tx.QueryRow(`SELECT key,COALESCE(news_id,0),version,title,body FROM news_push_jobs WHERE delivery_id=?`, d.ID).Scan(&d.Key, &d.NewsID, &version, &d.Title, &d.Body)
	if err == sql.ErrNoRows {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	n, err := readNewsPushItem(tx, d.NewsID)
	if err == sql.ErrNoRows {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if !p.matches(n.rule) {
		return false, nil
	}
	if n.version != version {
		var prior string
		if err = tx.QueryRow(`SELECT material FROM news_push_links WHERE news_id=?`, d.NewsID).Scan(&prior); err != nil {
			if err == sql.ErrNoRows {
				return false, nil
			}
			return false, err
		}
		if prior != newsMaterial(n.title, n.content) {
			return false, nil
		}
		d.Title = n.title
		if _, err = tx.Exec(`UPDATE news_push_jobs SET version=?,title=? WHERE delivery_id=?`, n.version, n.title, d.ID); err != nil {
			return false, err
		}
	}
	if d.Receipt == "" {
		d.Receipt = token()
		_, err = tx.Exec(`UPDATE deliveries SET receipt_token=?,receipt_hash=? WHERE id=?`, d.Receipt, digest(d.Receipt), d.ID)
	}
	return true, err
}
