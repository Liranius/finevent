package probe

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"time"

	"finevent/internal/calendar"
)

func (a *App) reminderRoutes(m *http.ServeMux) {
	m.HandleFunc("GET "+prefix+"reminders", a.reminderStatus)
	m.HandleFunc("PUT "+prefix+"reminders/settings", func(w http.ResponseWriter, r *http.Request) {
		var p ReminderSettings
		if decode(w, r, &p) != nil {
			fail(w, 400, "提醒设置格式无效")
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
		raw, err := json.Marshal(p)
		if err == nil {
			_, err = tx.Exec(`INSERT INTO calendar_reminder_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload`, string(raw))
		}
		if err == nil {
			err = reconcileReminders(tx, time.Now().Unix())
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
	m.HandleFunc("GET "+prefix+"reminders/events/{id}", a.reminderEvent)
	for _, method := range []string{"PUT", "DELETE"} {
		m.HandleFunc(method+" "+prefix+"reminders/events/{id}", func(w http.ResponseWriter, r *http.Request) {
			var input struct {
				Enabled *bool `json:"enabled"`
			}
			if r.Method == "PUT" && (decode(w, r, &input) != nil || input.Enabled == nil) {
				fail(w, 400, "请选择关注状态")
				return
			}
			tx, err := a.Store.DB.Begin()
			if err != nil {
				a.internal(w, err)
				return
			}
			defer tx.Rollback()
			var exists int
			if err = tx.QueryRow(`SELECT 1 FROM calendar_events WHERE id=?`, r.PathValue("id")).Scan(&exists); err == sql.ErrNoRows {
				fail(w, 404, "事件不存在")
				return
			}
			if err != nil {
				a.internal(w, err)
				return
			}
			if r.Method == "DELETE" {
				_, err = tx.Exec(`DELETE FROM calendar_reminder_watches WHERE event_id=?`, r.PathValue("id"))
			} else {
				_, err = tx.Exec(`INSERT INTO calendar_reminder_watches VALUES(?,?) ON CONFLICT(event_id) DO UPDATE SET enabled=excluded.enabled`, r.PathValue("id"), *input.Enabled)
			}
			if err == nil {
				err = reconcileReminders(tx, time.Now().Unix())
			}
			if err == nil {
				err = tx.Commit()
			}
			if err != nil {
				a.internal(w, err)
				return
			}
			writeJSON(w, 200, map[string]bool{"ok": true})
		})
	}
}

func (a *App) reminderStatus(w http.ResponseWriter, r *http.Request) {
	p, err := reminderSettings(a.Store.DB)
	if err != nil {
		a.internal(w, err)
		return
	}
	result := map[string]any{"settings": p}
	queries := map[string]string{
		"devices":  `SELECT id,label,active FROM subscriptions ORDER BY id`,
		"watches":  `SELECT w.event_id,w.enabled,json_extract(e.payload,'$.title') AS title FROM calendar_reminder_watches w JOIN calendar_events e ON e.id=w.event_id ORDER BY e.start_day DESC LIMIT 100`,
		"upcoming": `SELECT d.id,j.event_id,j.title,j.body,j.scheduled_at,d.due,d.status,s.label,d.accepted_at,d.received_at,d.clicked_at,d.error FROM calendar_reminder_jobs j JOIN deliveries d ON d.id=j.delivery_id JOIN subscriptions s ON s.id=d.subscription_id WHERE d.status IN ('queued','sending') ORDER BY j.scheduled_at LIMIT 30`,
		"history":  `SELECT d.id,j.event_id,j.title,j.body,j.scheduled_at,d.due,d.status,s.label,d.accepted_at,d.received_at,d.clicked_at,d.error FROM calendar_reminder_jobs j JOIN deliveries d ON d.id=j.delivery_id JOIN subscriptions s ON s.id=d.subscription_id WHERE d.status NOT IN ('queued','sending') ORDER BY j.scheduled_at DESC,d.id DESC LIMIT 30`,
	}
	for key, query := range queries {
		v, err := queryMaps(a.Store.DB, query)
		if err != nil {
			a.internal(w, err)
			return
		}
		result[key] = v
	}
	writeJSON(w, 200, result)
}

func (a *App) reminderEvent(w http.ResponseWriter, r *http.Request) {
	s := &calendar.Store{DB: a.Store.DB}
	e, err := s.Get(r.PathValue("id"))
	if err == sql.ErrNoRows {
		fail(w, 404, "事件不存在")
		return
	}
	if err != nil {
		a.internal(w, err)
		return
	}
	p, err := reminderSettings(a.Store.DB)
	if err != nil {
		a.internal(w, err)
		return
	}
	follow, override, err := reminderFollow(a.Store.DB, p, e)
	if err != nil {
		a.internal(w, err)
		return
	}
	rows, err := queryMaps(a.Store.DB, `SELECT d.id,j.scheduled_at,s.label,d.status FROM calendar_reminder_jobs j JOIN deliveries d ON d.id=j.delivery_id JOIN subscriptions s ON s.id=d.subscription_id WHERE j.event_id=? AND d.status IN ('queued','sending') ORDER BY j.scheduled_at LIMIT 12`, e.ID)
	if err != nil {
		a.internal(w, err)
		return
	}
	var active int
	for _, id := range p.Devices {
		var n int
		if err = a.Store.DB.QueryRow(`SELECT active FROM subscriptions WHERE id=?`, id).Scan(&n); err != nil && err != sql.ErrNoRows {
			a.internal(w, err)
			return
		}
		active += n
	}
	writeJSON(w, 200, map[string]any{"followed": follow, "override": override, "enabled": p.Enabled, "active_devices": active, "upcoming": rows})
}
