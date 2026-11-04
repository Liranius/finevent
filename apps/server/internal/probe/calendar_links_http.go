package probe

import (
	"database/sql"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"finevent/internal/calendar"
)

func (a *App) calendarLinkRoutes(m *http.ServeMux) {
	respond := func(w http.ResponseWriter, err error) {
		switch {
		case errors.Is(err, sql.ErrNoRows):
			fail(w, 404, "报道或日程不存在，可能已超过保留期")
		case errors.Is(err, calendar.ErrConflict):
			fail(w, 409, "报道、日程或关联已变更，请刷新后重新核对")
		case errors.Is(err, errLinkInput):
			fail(w, 400, err.Error())
		default:
			a.internal(w, err)
		}
	}
	m.HandleFunc("GET "+prefix+"calendar-link-search", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		out, err := a.Store.SearchCalendarLinks(r.Context(), q.Get("from"), q.Get("to"), strings.TrimSpace(q.Get("q")))
		if err != nil {
			respond(w, err)
			return
		}
		writeJSON(w, 200, out)
	})
	for _, path := range []string{"news/{id}/calendar-links", "calendar/{event}/news-links"} {
		m.HandleFunc("GET "+prefix+path, func(w http.ResponseWriter, r *http.Request) {
			id, _ := strconv.ParseInt(r.PathValue("id"), 10, 64)
			before, err := strconv.ParseInt(r.URL.Query().Get("before"), 10, 64)
			if (r.PathValue("event") == "" && id <= 0) || before < 0 || (r.URL.Query().Get("before") != "" && err != nil) {
				respond(w, errLinkInput)
				return
			}
			out, err := a.Store.CalendarLinks(r.Context(), id, r.PathValue("event"), before)
			if err != nil {
				respond(w, err)
				return
			}
			writeJSON(w, 200, out)
		})
	}
	for _, method := range []string{"PUT", "DELETE"} {
		m.HandleFunc(method+" "+prefix+"news/{id}/calendar-links/{event}", func(w http.ResponseWriter, r *http.Request) {
			id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
			var input CalendarLinkInput
			if err != nil || id <= 0 || decodeLimit(w, r, &input, 8192) != nil {
				respond(w, errLinkInput)
				return
			}
			if err = a.Store.SaveCalendarLink(r.Context(), id, r.PathValue("event"), input, r.Method == "DELETE"); err != nil {
				respond(w, err)
				return
			}
			writeJSON(w, 200, map[string]bool{"saved": true})
		})
	}
}
