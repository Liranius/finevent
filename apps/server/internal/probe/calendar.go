package probe

import (
	"database/sql"
	"errors"
	"net/http"
	"strings"

	"finevent/internal/calendar"
)

func (a *App) calendarRoutes(m *http.ServeMux) {
	s := &calendar.Store{DB: a.Store.DB}
	respondError := func(w http.ResponseWriter, err error) {
		if errors.Is(err, sql.ErrNoRows) {
			fail(w, 404, "记录不存在")
		} else if errors.Is(err, calendar.ErrConflict) {
			fail(w, 409, err.Error())
		} else {
			a.internal(w, err)
		}
	}
	m.HandleFunc("GET "+prefix+"calendar-snapshots/{id}", func(w http.ResponseWriter, r *http.Request) {
		body, err := s.SnapshotBody(r.PathValue("id"))
		if err != nil {
			respondError(w, err)
			return
		}
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.Header().Set("Content-Disposition", `attachment; filename="official-calendar-snapshot.txt"`)
		_, _ = w.Write(body)
	})
	m.HandleFunc("GET "+prefix+"calendar", func(w http.ResponseWriter, r *http.Request) {
		from, to := r.URL.Query().Get("from"), r.URL.Query().Get("to")
		if err := calendar.DateRange(from, to); err != nil {
			fail(w, 400, err.Error())
			return
		}
		events, err := s.List(from, to)
		if err != nil {
			respondError(w, err)
			return
		}
		sources, err := s.Sources()
		if err != nil {
			respondError(w, err)
			return
		}
		writeJSON(w, 200, map[string]any{"events": events, "sources": sources, "sync_enabled": a.CollectionEnabled})
	})
	m.HandleFunc("GET "+prefix+"calendar/{id}", func(w http.ResponseWriter, r *http.Request) {
		e, err := s.Get(r.PathValue("id"))
		if err != nil {
			respondError(w, err)
			return
		}
		history, err := s.History(e.ID)
		if err != nil {
			respondError(w, err)
			return
		}
		writeJSON(w, 200, map[string]any{"event": e, "revisions": history})
	})
	m.HandleFunc("POST "+prefix+"calendar", func(w http.ResponseWriter, r *http.Request) {
		var e calendar.Event
		if decodeLimit(w, r, &e, 65536) != nil {
			fail(w, 400, "事件格式不正确")
			return
		}
		e.Source = "manual"
		e.SourceID = token()
		e.ID = ""
		e.Version = 0
		e.Provenance = nil
		e.Kind = "other"
		e.FundCode = ""
		if strings.TrimSpace(e.Evidence) == "" {
			fail(w, 400, "请填写支持该事件的原文依据")
			return
		}
		if err := e.Validate(); err != nil {
			fail(w, 400, err.Error())
			return
		}
		saved, err := s.Save(e, 0)
		if err != nil {
			respondError(w, err)
			return
		}
		writeJSON(w, 201, saved)
	})
	m.HandleFunc("PUT "+prefix+"calendar/{id}", func(w http.ResponseWriter, r *http.Request) {
		var e calendar.Event
		if decodeLimit(w, r, &e, 65536) != nil {
			fail(w, 400, "事件格式不正确")
			return
		}
		old, err := s.Get(r.PathValue("id"))
		if err != nil {
			respondError(w, err)
			return
		}
		if old.Source != "manual" && old.Source != "fund" {
			fail(w, 403, "官方日程由来源同步更新")
			return
		}
		e.ID = old.ID
		e.Source = old.Source
		e.SourceID = old.SourceID
		e.Kind = old.Kind
		e.Evidence = old.Evidence
		e.URL = old.URL
		e.Provenance = old.Provenance
		if old.Source == "fund" {
			e.Precision = "date"
			e.Timezone = "Asia/Shanghai"
		}
		if err = e.Validate(); err != nil {
			fail(w, 400, err.Error())
			return
		}
		saved, err := s.Save(e, e.Version)
		if err != nil {
			respondError(w, err)
			return
		}
		writeJSON(w, 200, saved)
	})
	m.HandleFunc("POST "+prefix+"fund-drafts", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Text string `json:"text"`
			URL  string `json:"url"`
		}
		if decodeLimit(w, r, &input, 65536) != nil || len(strings.TrimSpace(input.Text)) < 20 || len(input.Text) > 12000 || !calendar.ValidURL(input.URL) {
			fail(w, 400, "请填写公告原文（20–12000 字节）及有效原文链接")
			return
		}
		d := calendar.ExtractFund(token(), strings.TrimSpace(input.Text), input.URL)
		if err := s.SaveDraft(d); err != nil {
			respondError(w, err)
			return
		}
		writeJSON(w, 201, d)
	})
	m.HandleFunc("GET "+prefix+"fund-drafts/{id}", func(w http.ResponseWriter, r *http.Request) {
		d, err := s.Draft(r.PathValue("id"))
		if err != nil {
			respondError(w, err)
			return
		}
		writeJSON(w, 200, d)
	})
	m.HandleFunc("GET "+prefix+"fund-drafts", func(w http.ResponseWriter, r *http.Request) {
		rows, err := a.Store.DB.Query(`SELECT id FROM fund_drafts WHERE json_extract(payload,'$.confirmed_id')='' ORDER BY created DESC LIMIT 30`)
		if err != nil {
			respondError(w, err)
			return
		}
		ids := []string{}
		for rows.Next() {
			var id string
			if err = rows.Scan(&id); err != nil {
				break
			}
			ids = append(ids, id)
		}
		rowErr := rows.Err()
		rows.Close()
		if err != nil || rowErr != nil {
			a.internal(w, errors.New("draft list failed"))
			return
		}
		drafts := []calendar.Draft{}
		for _, id := range ids {
			d, err := s.Draft(id)
			if err != nil {
				respondError(w, err)
				return
			}
			if d.ConfirmedID == "" {
				drafts = append(drafts, d)
			}
		}
		writeJSON(w, 200, drafts)
	})
	m.HandleFunc("POST "+prefix+"fund-drafts/{id}/confirm", func(w http.ResponseWriter, r *http.Request) {
		var input struct {
			Event        calendar.Event `json:"event"`
			Acknowledged bool           `json:"acknowledged"`
		}
		if decodeLimit(w, r, &input, 65536) != nil || !input.Acknowledged {
			fail(w, 400, "请先核对原文并确认")
			return
		}
		// Validate editable fields before entering the transaction. Provenance always comes from the saved draft.
		d, err := s.Draft(r.PathValue("id"))
		if err != nil {
			respondError(w, err)
			return
		}
		e := input.Event
		e.Kind = "fund"
		e.Precision = "date"
		e.Timezone = "Asia/Shanghai"
		e.Status = "confirmed"
		e.URL = d.URL
		e.Evidence = d.Text
		if err = e.Validate(); err != nil {
			fail(w, 400, err.Error())
			return
		}
		saved, err := s.Confirm(d.ID, e)
		if err != nil {
			respondError(w, err)
			return
		}
		writeJSON(w, 200, saved)
	})
}
