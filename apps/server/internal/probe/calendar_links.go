package probe

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"finevent/internal/calendar"
)

var errLinkInput = errors.New("请填写关联依据，并选择有效的新闻和日程版本")

func initCalendarLinks(db *sql.DB) error {
	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS news_calendar_links(
id INTEGER PRIMARY KEY AUTOINCREMENT,
news_id INTEGER NOT NULL REFERENCES news(id) ON DELETE CASCADE,
event_id TEXT NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
note TEXT NOT NULL, confirmed_at INTEGER NOT NULL,
news_snapshot TEXT NOT NULL, event_snapshot TEXT NOT NULL,
UNIQUE(news_id,event_id));
CREATE INDEX IF NOT EXISTS news_calendar_event ON news_calendar_links(event_id,id DESC);`)
	return err
}

type CalendarLink struct {
	ID            int64          `json:"id"`
	News          NewsEntry      `json:"news"`
	Event         calendar.Event `json:"event"`
	Note          string         `json:"note"`
	ConfirmedAt   int64          `json:"confirmed_at"`
	NewsSnapshot  NewsEntry      `json:"news_snapshot"`
	EventSnapshot calendar.Event `json:"event_snapshot"`
	Changed       bool           `json:"changed"`
}

type CalendarLinkPage struct {
	Items []CalendarLink `json:"items"`
	Next  int64          `json:"next"`
}

type CalendarLinkInput struct {
	Revision     int64  `json:"revision"`
	NewsVersion  int    `json:"news_version"`
	EventVersion int    `json:"event_version"`
	Note         string `json:"note"`
}

// Reads and confirmations use one transaction: no association can be confirmed
// against a revision different from the one the user actually reviewed.
func (s *Store) SaveCalendarLink(ctx context.Context, newsID int64, eventID string, input CalendarLinkInput, remove bool) error {
	input.Note = strings.TrimSpace(input.Note)
	if input.Revision < 0 || (!remove && (input.NewsVersion < 1 || input.EventVersion < 1 || len([]rune(input.Note)) < 5 || len([]rune(input.Note)) > 1000)) {
		return errLinkInput
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	n, err := scanNews(tx.QueryRowContext(ctx, `SELECT `+newsColumns+` FROM news n LEFT JOIN news_features f ON f.news_id=n.id WHERE n.id=?`, newsID))
	if err != nil {
		return err
	}
	var event calendar.Event
	var payload string
	if err = tx.QueryRowContext(ctx, `SELECT payload FROM calendar_events WHERE id=?`, eventID).Scan(&payload); err != nil {
		return err
	}
	if err = json.Unmarshal([]byte(payload), &event); err != nil {
		return err
	}
	var revision int64
	err = tx.QueryRowContext(ctx, `SELECT id FROM news_calendar_links WHERE news_id=? AND event_id=?`, newsID, eventID).Scan(&revision)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	if revision != input.Revision || (remove && revision == 0) {
		return calendar.ErrConflict
	}
	if !remove {
		if n.Version != input.NewsVersion || event.Version != input.EventVersion {
			return calendar.ErrConflict
		}
		var count int
		if err = tx.QueryRowContext(ctx, `SELECT count(*) FROM news_calendar_links WHERE news_id=?`, newsID).Scan(&count); err != nil {
			return err
		}
		if count >= 20 && revision == 0 {
			return errors.Join(errLinkInput, errors.New("每条报道最多关联 20 项日程"))
		}
	}
	if _, err = tx.ExecContext(ctx, `DELETE FROM news_calendar_links WHERE news_id=? AND event_id=?`, newsID, eventID); err != nil {
		return err
	}
	if !remove {
		// Each confirmation gets a new, never-reused revision, including remove/re-add.
		body, err := json.Marshal(n)
		if err != nil {
			return err
		}
		if _, err = tx.ExecContext(ctx, `INSERT INTO news_calendar_links(news_id,event_id,note,confirmed_at,news_snapshot,event_snapshot) VALUES(?,?,?,?,?,?)`, newsID, eventID, input.Note, time.Now().Unix(), string(body), payload); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (s *Store) CalendarLinks(ctx context.Context, newsID int64, eventID string, before int64) (CalendarLinkPage, error) {
	out := CalendarLinkPage{Items: []CalendarLink{}}
	tx, err := s.DB.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return out, err
	}
	defer tx.Rollback()
	var exists int
	where, arg := "l.news_id=?", any(newsID)
	if eventID != "" {
		where, arg = "l.event_id=?", eventID
		err = tx.QueryRowContext(ctx, `SELECT 1 FROM calendar_events WHERE id=?`, eventID).Scan(&exists)
	} else {
		err = tx.QueryRowContext(ctx, `SELECT 1 FROM news WHERE id=?`, newsID).Scan(&exists)
	}
	if err != nil {
		return out, err
	}
	rows, err := tx.QueryContext(ctx, `SELECT l.id,l.news_id,l.note,l.confirmed_at,l.news_snapshot,l.event_snapshot,e.payload FROM news_calendar_links l JOIN calendar_events e ON e.id=l.event_id WHERE `+where+` AND (?=0 OR l.id<?) ORDER BY l.id DESC LIMIT 31`, arg, before, before)
	if err != nil {
		return out, err
	}
	ids := []int64{}
	for rows.Next() {
		var link CalendarLink
		var id int64
		var n, old, current string
		if err = rows.Scan(&link.ID, &id, &link.Note, &link.ConfirmedAt, &n, &old, &current); err != nil {
			break
		}
		if err = json.Unmarshal([]byte(n), &link.NewsSnapshot); err != nil {
			break
		}
		if err = json.Unmarshal([]byte(old), &link.EventSnapshot); err != nil {
			break
		}
		if err = json.Unmarshal([]byte(current), &link.Event); err != nil {
			break
		}
		out.Items = append(out.Items, link)
		ids = append(ids, id)
	}
	rowErr := rows.Err()
	rows.Close()
	if err != nil {
		return out, err
	}
	if rowErr != nil {
		return out, rowErr
	}
	if len(out.Items) > 30 {
		out.Items = out.Items[:30]
		out.Next = out.Items[29].ID
	}
	for i := range out.Items {
		link := &out.Items[i]
		link.News, err = scanNews(tx.QueryRowContext(ctx, `SELECT `+newsListColumns+` FROM news n LEFT JOIN news_features f ON f.news_id=n.id WHERE n.id=?`, ids[i]))
		if err != nil {
			return out, err
		}
		link.Changed = link.News.Version != link.NewsSnapshot.Version || link.Event.Version != link.EventSnapshot.Version
	}
	return out, tx.Commit()
}

type CalendarLinkSearch struct {
	Items []calendar.Event `json:"items"`
	More  bool             `json:"more"`
}

func (s *Store) SearchCalendarLinks(ctx context.Context, from, to, query string) (CalendarLinkSearch, error) {
	out := CalendarLinkSearch{Items: []calendar.Event{}}
	if calendar.DateRange(from, to) != nil || len([]rune(query)) > 100 {
		return out, errLinkInput
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT payload FROM calendar_events WHERE start_day<=? AND end_day>=? AND (instr(lower(json_extract(payload,'$.title')),lower(?))>0 OR instr(json_extract(payload,'$.fund_code'),?)>0) ORDER BY start_day,id LIMIT 31`, to, from, query, query)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var payload string
		var event calendar.Event
		if err = rows.Scan(&payload); err != nil {
			return out, err
		}
		if err = json.Unmarshal([]byte(payload), &event); err != nil {
			return out, err
		}
		out.Items = append(out.Items, event)
	}
	if len(out.Items) > 30 {
		out.More = true
		out.Items = out.Items[:30]
	}
	return out, rows.Err()
}
