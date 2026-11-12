package probe

import (
	"database/sql"
	"errors"
	"net/http"
	"strconv"
)

func (a *App) newsRoutes(m *http.ServeMux) {
	a.calendarLinkRoutes(m)
	m.HandleFunc("GET "+prefix+"news/meta", func(w http.ResponseWriter, r *http.Request) {
		result, err := a.Store.NewsMeta(r.Context())
		if err != nil {
			a.internal(w, err)
			return
		}
		result["collection_enabled"] = a.CollectionEnabled
		result["trendradar_enabled"] = a.TrendEnabled
		writeJSON(w, 200, result)
	})
	m.HandleFunc("GET "+prefix+"news/changes", func(w http.ResponseWriter, r *http.Request) {
		after, err := strconv.ParseInt(r.URL.Query().Get("after"), 10, 64)
		if err != nil || after < 0 {
			fail(w, 400, "增量游标无效")
			return
		}
		result, err := a.Store.NewsChanges(r.Context(), after)
		if err != nil {
			a.internal(w, err)
			return
		}
		writeJSON(w, 200, result)
	})
	m.HandleFunc("GET "+prefix+"news", func(w http.ResponseWriter, r *http.Request) {
		v := r.URL.Query()
		since := int64(0)
		importance := 0
		var err error
		if v.Get("since") != "" {
			since, err = strconv.ParseInt(v.Get("since"), 10, 64)
			if err != nil {
				fail(w, 400, "时间筛选无效")
				return
			}
		}
		if v.Get("importance") != "" {
			importance, err = strconv.Atoi(v.Get("importance"))
			if err != nil {
				fail(w, 400, "重要性筛选无效")
				return
			}
		}
		order := v.Get("order")
		if order == "" {
			order = "discovered"
		}
		if len(v.Get("cursor")) > 1000 {
			fail(w, 400, "分页游标过长")
			return
		}
		result, err := a.Store.NewsFeed(r.Context(), NewsFilter{Source: v.Get("source"), Category: v.Get("category"), Industry: v.Get("industry"), Query: trimNewsQuery(v.Get("q")), Order: order, Cursor: v.Get("cursor"), Since: since, Importance: importance})
		if errors.Is(err, ErrNewsFilter) {
			fail(w, 400, err.Error())
			return
		}
		if err != nil {
			a.internal(w, err)
			return
		}
		writeJSON(w, 200, result)
	})
	m.HandleFunc("GET "+prefix+"news/{id}", func(w http.ResponseWriter, r *http.Request) {
		id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
		if err != nil || id <= 0 {
			fail(w, 400, "报道编号无效")
			return
		}
		result, err := a.Store.NewsDetail(r.Context(), id)
		if errors.Is(err, sql.ErrNoRows) {
			fail(w, 404, "报道不存在或已超过保留期")
			return
		}
		if err != nil {
			a.internal(w, err)
			return
		}
		writeJSON(w, 200, result)
	})
}
