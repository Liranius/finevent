package probe

import (
	"encoding/json"
	"net/http"
	"time"

	topics "finevent/internal/news"
)

func (a *App) newsPushRoutes(m *http.ServeMux) {
	m.HandleFunc("GET "+prefix+"news-push", a.newsPushStatus)
	m.HandleFunc("PUT "+prefix+"news-push/settings", func(w http.ResponseWriter, r *http.Request) {
		var p NewsPushSettings
		if decode(w, r, &p) != nil {
			fail(w, 400, "新闻推送设置格式无效")
			return
		}
		p.normalize()
		tx, err := a.Store.DB.Begin()
		if err != nil {
			a.internal(w, err)
			return
		}
		defer tx.Rollback()
		if err = p.validate(tx); err != nil {
			fail(w, 400, err.Error())
			return
		}
		previous, _, _, err := newsPushSettings(tx)
		if err != nil {
			a.internal(w, err)
			return
		}
		raw, _ := json.Marshal(p)
		old, _ := json.Marshal(previous)
		if string(raw) != string(old) {
			// Reset only on an effective preference change; saving twice is not a replay.
			_, err = tx.Exec(`INSERT INTO news_push_settings VALUES(1,?,(SELECT COALESCE(MAX(seq),0) FROM news_changes),?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,cursor=excluded.cursor,enabled_at=excluded.enabled_at`, string(raw), time.Now().Unix())
			if err == nil {
				_, err = tx.Exec(`UPDATE deliveries SET status='cancelled',error='新闻关注或设备设置已变更',receipt_token='' WHERE scenario='news' AND status='queued'`)
			}
		}
		if err == nil {
			err = tx.Commit()
		}
		if err != nil {
			a.internal(w, err)
			return
		}
		writeJSON(w, 200, p)
	})
}

func (a *App) newsPushStatus(w http.ResponseWriter, r *http.Request) {
	p, cursor, enabledAt, err := newsPushSettings(a.Store.DB)
	if err != nil {
		a.internal(w, err)
		return
	}
	result := map[string]any{"settings": p, "enabled_at": enabledAt, "categories": topics.Categories(), "industries": topics.Industries()}
	var backlog int
	if p.Enabled {
		err = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM news_changes WHERE seq>?`, cursor).Scan(&backlog)
		if err != nil {
			a.internal(w, err)
			return
		}
	}
	result["backlog"] = backlog
	for key, query := range map[string]string{
		"devices": `SELECT id,label,active FROM subscriptions ORDER BY id`,
		"history": `SELECT d.id,j.news_id,j.version,j.title,j.body,j.reason,j.created,d.due,d.expires,d.status,s.label,d.accepted_at,d.received_at,d.clicked_at,d.error FROM news_push_jobs j JOIN deliveries d ON d.id=j.delivery_id JOIN subscriptions s ON s.id=d.subscription_id ORDER BY j.created DESC,d.id DESC LIMIT 50`,
	} {
		rows, e := queryMaps(a.Store.DB, query)
		if e != nil {
			a.internal(w, e)
			return
		}
		result[key] = rows
	}
	writeJSON(w, 200, result)
}
