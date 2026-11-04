package probe

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"
)

func saveNews(t *testing.T, s *Store, items []Item, at int64) {
	t.Helper()
	if _, _, err := s.SaveItems(items, at); err != nil {
		t.Fatal(err)
	}
}
func TestNewsDedupRevisionAndEvidence(t *testing.T) {
	a := testApp(t)
	s := a.Store
	ctx := context.Background()
	now := time.Now().Unix()
	body := "央行宣布下调存款准备金率0.5个百分点，涉及银行流动性安排。完整细则以发布机构正式文件为准，本报道保留来源。"
	i := Item{Source: "wallstreetcn", SourceID: "one", Title: "央行宣布降准", Content: body, URL: "https://example.org/news?utm_source=a", PublishedAt: now - 60}
	saveNews(t, s, []Item{i}, now)
	saveNews(t, s, []Item{i}, now+86400)
	page, err := s.NewsFeed(ctx, NewsFilter{Order: "discovered"})
	if err != nil {
		t.Fatal(err)
	}
	if len(page.Items) != 1 || page.Items[0].Version != 1 || page.Items[0].IngestedAt != now || page.Watermark != 1 {
		t.Fatalf("%+v", page)
	}
	i.Source = "trendradar:cls-telegraph"
	i.SourceID = "two"
	i.PublishedAt = 0
	saveNews(t, s, []Item{i}, now+10)
	detail, err := s.NewsDetail(ctx, page.Items[0].ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(detail.Related) != 1 || detail.Related[0].Kind != "same_url" || detail.Related[0].Item.PublishedAt != 0 {
		t.Fatalf("%+v", detail)
	}
	i.Title = "公司发布业绩"
	i.Content = "不同正文"
	i.URL = "https://example.org/corrected"
	i.PublishedAt = now + 5
	saveNews(t, s, []Item{i}, now+20)
	updated, err := s.NewsFeed(ctx, NewsFilter{Order: "discovered", Source: i.Source, Category: "company"})
	if err != nil || len(updated.Items) != 1 {
		t.Fatalf("%+v %v", updated, err)
	}
	detail, err = s.NewsDetail(ctx, updated.Items[0].ID)
	if err != nil {
		t.Fatal(err)
	}
	if detail.Item.Version != 2 || detail.Item.UpdatedAt != now+20 || len(detail.Revisions) != 1 || detail.Revisions[0].PublishedAt == nil || *detail.Revisions[0].PublishedAt != 0 || *detail.Revisions[0].URL != "https://example.org/news" {
		t.Fatalf("%+v", detail)
	}
	monetary, _ := s.NewsFeed(ctx, NewsFilter{Order: "discovered", Source: i.Source, Category: "monetary"})
	if len(monetary.Items) != 0 {
		t.Fatal("old labels retained")
	}
}
func TestNewsPaginationFiltersAndChanges(t *testing.T) {
	a := testApp(t)
	s := a.Store
	ctx := context.Background()
	now := time.Now().Unix()
	items := []Item{}
	for i := 0; i < 35; i++ {
		items = append(items, Item{Source: "test", SourceID: fmt.Sprint(i), Title: fmt.Sprintf("银行关注央行降准消息%d", i), PublishedAt: now - int64(i)})
	}
	saveNews(t, s, items, now)
	q := NewsFilter{Order: "published", Category: "monetary", Industry: "finance", Importance: 3}
	first, err := s.NewsFeed(ctx, q)
	if err != nil || len(first.Items) != 30 || first.Next == "" {
		t.Fatalf("%+v %v", first, err)
	}
	saveNews(t, s, []Item{{Source: "test", SourceID: "new", Title: "央行降准，银行", PublishedAt: now + 1}}, now+1)
	q.Cursor = first.Next
	second, err := s.NewsFeed(ctx, q)
	if err != nil || len(second.Items) != 5 || second.Next != "" {
		t.Fatalf("%+v %v", second, err)
	}
	q.Category = "company"
	if _, err = s.NewsFeed(ctx, q); err != ErrNewsFilter {
		t.Fatal("cursor accepted with other filter", err)
	}
	changes, err := s.NewsChanges(ctx, first.Watermark)
	if err != nil || changes["changed"] != int64(1) {
		t.Fatalf("%+v %v", changes, err)
	}
	if w := perform(a, httptest.NewRequest("GET", prefix+"news", nil)); w.Code != 401 {
		t.Fatal(w.Code)
	}
	for _, path := range []string{"news?order=oops", "news?importance=4", "news?category=unknown", "news?cursor=broken", "news/changes?after=-1"} {
		if w := perform(a, authed(t, a, "GET", path, "")); w.Code != 400 {
			t.Fatal(path, w.Code, w.Body.String())
		}
	}
	if w := perform(a, authed(t, a, "GET", "news/999999", "")); w.Code != 404 {
		t.Fatal(w.Code)
	}
	response := perform(a, authed(t, a, "GET", "news?source=test", ""))
	var result NewsPage
	if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &result) != nil || len(result.Items) != 30 {
		t.Fatal(response.Body.String())
	}
}
func TestNewsLegacyBackfillAndPrune(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().Unix()
	saveNews(t, s, []Item{{Source: "old", SourceID: "old", Title: "CPI发布"}}, now)
	_, err = s.DB.Exec(`DROP TRIGGER news_deleted;DROP TRIGGER news_stable_identity;DROP TABLE news_identity;DROP TABLE news_labels;DROP TABLE news_processing;DROP TABLE news_changes;DROP TABLE news_features;DROP TABLE news_revision_evidence;ALTER TABLE news DROP COLUMN ingested_at;ALTER TABLE news DROP COLUMN updated_at;INSERT INTO news_revisions VALUES(1,0,'旧标题','旧正文',10);`)
	if err != nil {
		t.Fatal(err)
	}
	s.DB.Close()
	s, err = OpenStore(filepath.Clean(dir))
	if err != nil {
		t.Fatal(err)
	}
	defer s.DB.Close()
	ctx := context.Background()
	page, err := s.NewsFeed(ctx, NewsFilter{Order: "discovered"})
	if err != nil {
		t.Fatal(err)
	}
	if page.Backlog != 1 || page.Items[0].Classification != nil || page.Items[0].IngestedAt != 0 {
		t.Fatalf("%+v", page)
	}
	if n, err := s.ProcessNewsBatch(ctx, now); err != nil || n != 1 {
		t.Fatal(n, err)
	}
	detail, err := s.NewsDetail(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	if detail.Item.Classification == nil || detail.Revisions[0].PublishedAt != nil || detail.Revisions[0].URL != nil {
		t.Fatalf("%+v", detail)
	}
	if err = s.Prune(time.Unix(now, 0).AddDate(0, 0, 91)); err != nil {
		t.Fatal(err)
	}
	for _, table := range []string{"news_features", "news_labels", "news_changes", "news_revisions", "news_revision_evidence"} {
		var count int
		if err = s.DB.QueryRow("SELECT COUNT(*) FROM " + table).Scan(&count); err != nil || count != 0 {
			t.Fatal(table, count, err)
		}
	}
}

func TestNewsRollbackWriterAndDeletion(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	now := time.Now().Unix()
	saveNews(t, s, []Item{{Source: "legacy", SourceID: "1", Title: "CPI发布"}}, now)
	// Exact positional revision statement used by the previous release must still work.
	_, err = s.DB.Exec(`INSERT INTO news_revisions SELECT id,version,title,content,? FROM news WHERE id=1; UPDATE news SET title='银行增持',version=version+1 WHERE id=1`, now+1)
	if err != nil {
		t.Fatal(err)
	}
	s.DB.Close()
	s, err = OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer s.DB.Close()
	if count, err := s.ProcessNewsBatch(ctx, now+2); err != nil || count != 1 {
		t.Fatal(count, err)
	}
	detail, err := s.NewsDetail(ctx, 1)
	if err != nil {
		t.Fatal(err)
	}
	if detail.Item.UpdatedAt != 0 || detail.Item.Classification.Categories[0] != "company" || detail.Revisions[0].URL != nil {
		t.Fatalf("%+v", detail)
	}
	page, _ := s.NewsFeed(ctx, NewsFilter{Order: "discovered"})
	if _, err = s.DB.Exec(`DELETE FROM news WHERE id=1`); err != nil {
		t.Fatal(err)
	}
	changes, err := s.NewsChanges(ctx, page.Watermark)
	if err != nil || changes["changed"] != int64(1) {
		t.Fatal(changes, err)
	}
	saveNews(t, s, []Item{{Source: "legacy", SourceID: "new", Title: "另一条报道"}}, now+3)
	page, err = s.NewsFeed(ctx, NewsFilter{Order: "discovered"})
	if err != nil || len(page.Items) != 1 || page.Items[0].ID <= 1 || page.Items[0].Classification == nil {
		t.Fatal(page, err)
	}
	if _, err = s.NewsDetail(ctx, 1); err != sql.ErrNoRows {
		t.Fatal("deleted link reused", err)
	}
	// An old binary still uses a column-list INSERT with SQLite's automatic rowid.
	if _, err = s.DB.Exec(`DELETE FROM news;INSERT INTO news(source,source_id,title,content,url,published_at,first_seen,last_seen,hash) VALUES('legacy','old-writer','旧版新报道','','',0,1,1,'new')`); err != nil {
		t.Fatal(err)
	}
	var newID int64
	if err = s.DB.QueryRow(`SELECT id FROM news`).Scan(&newID); err != nil || newID <= page.Items[0].ID {
		t.Fatal(newID, err)
	}
}

func TestNewsBackfillBatchIsBoundedAndResumes(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 26; i++ {
		_, err = s.DB.Exec(`INSERT INTO news(source,source_id,title,content,url,published_at,first_seen,last_seen,hash) VALUES('old',?,'央行降准','','',0,1,1,'old')`, fmt.Sprint(i))
		if err != nil {
			t.Fatal(err)
		}
	}
	s.DB.Close()
	s, err = OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	if count, err := s.ProcessNewsBatch(ctx, 2); err != nil || count != 25 {
		t.Fatal(count, err)
	}
	s.DB.Close()
	s, err = OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer s.DB.Close()
	if count, err := s.ProcessNewsBatch(ctx, 3); err != nil || count != 1 {
		t.Fatal(count, err)
	}
	page, err := s.NewsFeed(ctx, NewsFilter{Order: "published", Category: "monetary"})
	if err != nil || page.Backlog != 0 || len(page.Items) != 26 {
		t.Fatal(page, err)
	}
}

func TestNewsRelatedContentAndNumericDifferences(t *testing.T) {
	s := testApp(t).Store
	now := time.Now().Unix()
	body := "发布机构表示本条公告包含完整政策说明，具体执行细则以及后续安排仍以官方网站公布的信息为准。"
	items := []Item{
		{Source: "a", SourceID: "1", Title: "央行宣布下调存款准备金率0.5个百分点", Content: body},
		{Source: "b", SourceID: "2", Title: "同正文另一报道", Content: body},
		{Source: "c", SourceID: "3", Title: "央行：宣布下调存款准备金率0.5个百分点"},
		{Source: "d", SourceID: "4", Title: "央行宣布下调存款准备金率0.25个百分点"},
	}
	saveNews(t, s, items, now)
	detail, err := s.NewsDetail(context.Background(), 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(detail.Related) != 2 || detail.Related[0].Kind != "same_content" || detail.Related[1].Kind != "similar_title" {
		t.Fatalf("%+v", detail.Related)
	}
	page, err := s.NewsFeed(context.Background(), NewsFilter{Order: "published"})
	if err != nil || len(page.Items) != 4 || page.Items[0].PublishedAt != 0 || page.Items[0].Content != "" {
		t.Fatal(page, err)
	}
}

func TestNewsChannelRecoveryKeepsEarlierGap(t *testing.T) {
	a := testApp(t)
	for _, p := range []Poll{{Source: "wallstreetcn", Started: 1, Completed: 2, Success: false, Gap: "测试窗口缺口", Error: "timeout"}, {Source: "wallstreetcn", Started: 3, Completed: 4, Success: true}} {
		if err := a.Store.SavePoll(p); err != nil {
			t.Fatal(err)
		}
	}
	w := perform(a, authed(t, a, "GET", "news/meta", ""))
	var response struct {
		Channels []NewsChannel `json:"channels"`
	}
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &response) != nil || len(response.Channels) != 2 {
		t.Fatal(w.Code, w.Body.String())
	}
	c := response.Channels[0]
	if !c.Success || c.LastSuccess != 4 || c.GapAt != 2 || c.Gap != "测试窗口缺口" || c.Error != "" {
		t.Fatalf("%+v", c)
	}
}
