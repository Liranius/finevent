package probe

import (
	"net/http"
	"strconv"
	"strings"
	"time"
)

func (a *App) sourceHealthRoutes(m *http.ServeMux) {
	m.HandleFunc("GET "+prefix+"news/source-health", func(w http.ResponseWriter, r *http.Request) {
		result, err := a.Store.TrendHealth(r.Context(), time.Now().Unix())
		if err != nil {
			a.internal(w, err)
			return
		}
		result.CollectionEnabled = a.CollectionEnabled
		result.TrendEnabled = a.TrendEnabled
		writeJSON(w, 200, result)
	})
	m.HandleFunc("GET "+prefix+"news/source-failures", func(w http.ResponseWriter, r *http.Request) {
		source := r.URL.Query().Get("source")
		if len(source) > 128 {
			fail(w, 400, "来源筛选无效")
			return
		}
		var at, id int64
		if cursor := r.URL.Query().Get("before"); cursor != "" {
			parts := strings.Split(cursor, ":")
			if len(parts) != 2 || len(cursor) > 42 {
				fail(w, 400, "历史游标无效")
				return
			}
			var e1, e2 error
			at, e1 = strconv.ParseInt(parts[0], 10, 64)
			id, e2 = strconv.ParseInt(parts[1], 10, 64)
			if e1 != nil || e2 != nil || at <= 0 || id <= 0 {
				fail(w, 400, "历史游标无效")
				return
			}
		}
		result, err := a.Store.TrendFailures(r.Context(), source, at, id)
		if err != nil {
			a.internal(w, err)
			return
		}
		writeJSON(w, 200, result)
	})
}
