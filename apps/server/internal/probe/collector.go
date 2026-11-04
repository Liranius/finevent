package probe

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const WallstreetURL = "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=30"

var tags = regexp.MustCompile(`<[^>]*>`)

type wallResponse struct {
	Data struct {
		Items []struct {
			ID          int64  `json:"id"`
			Title       string `json:"title"`
			Content     string `json:"content_text"`
			URI         string `json:"uri"`
			DisplayTime int64  `json:"display_time"`
		} `json:"items"`
	} `json:"data"`
}

func FetchWallstreet(ctx context.Context, client *http.Client, address string) ([]Item, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "finevent/0.1 personal-source-observer")
	res, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("source HTTP %d", res.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(res.Body, 4*1024*1024+1))
	if err != nil {
		return nil, err
	}
	if len(body) > 4*1024*1024 {
		return nil, errors.New("source response exceeds 4 MiB")
	}
	var payload wallResponse
	if err = json.Unmarshal(body, &payload); err != nil {
		return nil, err
	}
	if len(payload.Data.Items) == 0 {
		return nil, errors.New("empty source window; source availability unconfirmed")
	}
	items := []Item{}
	for _, raw := range payload.Data.Items {
		content := strings.TrimSpace(html.UnescapeString(tags.ReplaceAllString(raw.Content, "")))
		title := strings.TrimSpace(raw.Title)
		if title == "" {
			title = content
		}
		if raw.ID <= 0 || title == "" {
			continue
		}
		if len(content) > 16000 {
			content = string([]rune(content)[:min(4000, len([]rune(content)))])
		}
		items = append(items, Item{Source: "wallstreetcn", SourceID: strconv.FormatInt(raw.ID, 10), Title: title, Content: content, URL: raw.URI, PublishedAt: raw.DisplayTime})
	}
	if len(items) == 0 {
		return nil, errors.New("no valid source items")
	}
	return items, nil
}

func (a *App) CollectWallstreet(ctx context.Context, address string) Poll {
	now := time.Now()
	p := Poll{Source: "wallstreetcn", Started: now.Unix()}
	items, err := FetchWallstreet(ctx, a.Client, address)
	if err != nil {
		p.Completed = time.Now().Unix()
		p.Error = err.Error()
		return p
	}
	var count int
	if err = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM news WHERE source='wallstreetcn'`).Scan(&count); err != nil {
		p.Error = err.Error()
		p.Completed = time.Now().Unix()
		return p
	}
	if count == 0 {
		for i := range items {
			items[i].Baseline = true
		}
	} else {
		// Bounded overlap recovery. A provider ignoring cursor cannot silently count as complete.
		overlap := false
		for page := 0; page < 4; page++ {
			for _, i := range items {
				var n int
				if err = a.Store.DB.QueryRow(`SELECT COUNT(*) FROM news WHERE source=? AND source_id=?`, i.Source, i.SourceID).Scan(&n); err != nil {
					break
				}
				if n > 0 {
					overlap = true
					break
				}
			}
			if overlap || err != nil {
				break
			}
			if page == 3 {
				break
			}
			oldest := items[len(items)-1]
			u, _ := url.Parse(address)
			q := u.Query()
			q.Set("cursor", strconv.FormatInt(oldest.PublishedAt, 10))
			u.RawQuery = q.Encode()
			more, e := FetchWallstreet(ctx, a.Client, u.String())
			if e != nil {
				p.Gap = "采集窗口无重叠，补采失败"
				break
			}
			if more[len(more)-1].PublishedAt >= oldest.PublishedAt {
				p.Gap = "采集窗口无重叠，来源未提供更早记录"
				break
			}
			items = append(items, more...)
		}
		if !overlap && p.Gap == "" {
			p.Gap = "补采上限内未找到历史重叠，存在未确认缺口"
		}
	}
	if err == nil {
		p.Added, p.Changed, err = a.Store.SaveItems(items, time.Now().Unix())
	}
	p.Completed = time.Now().Unix()
	p.Success = err == nil
	if err != nil {
		p.Error = err.Error()
	}
	return p
}

func ReadTrendRadar(root string, now time.Time) ([]Item, error) {
	zone, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return nil, err
	}
	items := []Item{}
	found := false
	for _, day := range []time.Time{now.In(zone).AddDate(0, 0, -1), now.In(zone)} {
		date := day.Format("2006-01-02")
		path := filepath.Join(root, "news", date+".db")
		if _, err = os.Stat(path); os.IsNotExist(err) {
			continue
		} else if err != nil {
			return nil, err
		}
		found = true
		absolute, e := filepath.Abs(path)
		if e != nil {
			return nil, e
		}
		uriPath := filepath.ToSlash(absolute)
		if filepath.VolumeName(absolute) != "" {
			uriPath = "/" + uriPath
		}
		u := url.URL{Scheme: "file", Path: uriPath, RawQuery: "mode=ro&_pragma=busy_timeout%28500%29"}
		db, e := sql.Open("sqlite", u.String())
		if e != nil {
			return nil, e
		}
		db.SetMaxOpenConns(1)
		rows, e := db.Query(`SELECT id,title,platform_id,COALESCE(url,''),first_crawl_time,last_crawl_time FROM news_items`)
		if e != nil {
			db.Close()
			return nil, e
		}
		for rows.Next() {
			var id int64
			var title, platform, address, first, last string
			if e = rows.Scan(&id, &title, &platform, &address, &first, &last); e != nil {
				break
			}
			key := CanonicalURL(address)
			if key == "" {
				key = date + ":" + strconv.FormatInt(id, 10)
			}
			observed, e2 := time.ParseInLocation("2006-01-02 15-04", date+" "+first, zone)
			if e2 != nil {
				e = e2
				break
			}
			latest, e2 := time.ParseInLocation("2006-01-02 15-04", date+" "+last, zone)
			if e2 != nil {
				e = e2
				break
			}
			items = append(items, Item{Source: "trendradar:" + platform, SourceID: key, Title: title, URL: address, FirstSeen: observed.Unix(), SourceObservedAt: latest.Unix()})
		}
		if e == nil {
			e = rows.Err()
		}
		rows.Close()
		db.Close()
		if e != nil {
			return nil, e
		}
	}
	if !found {
		return nil, errors.New("today/yesterday TrendRadar database not found")
	}
	return items, nil
}

func (a *App) CollectTrend(root string) Poll {
	a.syncTrendHealth(root, time.Now())
	p := Poll{Source: "trendradar", Started: time.Now().Unix()}
	items, err := ReadTrendRadar(root, time.Now())
	if err == nil {
		p.Added, p.Changed, err = a.Store.SaveItems(items, time.Now().Unix())
		var latest int64
		for _, i := range items {
			latest = max(latest, i.SourceObservedAt)
		}
		if time.Now().Unix()-latest > 40*60 {
			p.Gap = "TrendRadar 最新条目采集超过 40 分钟，请检查上游定时任务"
		}
	}
	p.Completed = time.Now().Unix()
	p.Success = err == nil
	if err != nil {
		p.Error = err.Error()
	}
	return p
}
