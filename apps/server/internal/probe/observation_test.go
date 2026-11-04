package probe

import (
	"context"
	"net/http"
	"os"
	"testing"
	"time"
)

func TestOlderSourceCannotOverwriteNewerRevision(t *testing.T) {
	a := testApp(t)
	now := time.Now().Unix()
	old := Item{Source: "trendradar:cls", SourceID: "url", Title: "old", FirstSeen: now - 86400, SourceObservedAt: now - 86400}
	latest := old
	latest.Title = "new"
	latest.SourceObservedAt = now
	if _, _, e := a.Store.SaveItems([]Item{old, latest}, now); e != nil {
		t.Fatal(e)
	}
	if n, c, e := a.Store.SaveItems([]Item{old, latest}, now+60); e != nil || n != 0 || c != 0 {
		t.Fatalf("repeat import: %d %d %v", n, c, e)
	}
	var title string
	var version int
	if e := a.Store.DB.QueryRow(`SELECT title,version FROM news`).Scan(&title, &version); e != nil || title != "new" || version != 2 {
		t.Fatal(title, version, e)
	}
}

func TestObservationPersistenceAndRetentionWindows(t *testing.T) {
	a := testApp(t)
	now := time.Now()
	if e := a.Store.SavePoll(Poll{Source: "wallstreetcn", Started: now.Unix(), Completed: now.Unix(), Success: true, Added: 3}); e != nil {
		t.Fatal(e)
	}
	if a.due("wallstreetcn", 2*time.Minute) {
		t.Fatal("successful poll was not persisted")
	}
	for _, at := range []int64{now.AddDate(0, 0, -8).Unix(), now.AddDate(0, 0, -1).Unix()} {
		if _, e := a.Store.DB.Exec(`INSERT INTO resources VALUES(?,1,1,1,1,1,0)`, at); e != nil {
			t.Fatal(e)
		}
	}
	if e := a.Store.Prune(now); e != nil {
		t.Fatal(e)
	}
	var n int
	if e := a.Store.DB.QueryRow(`SELECT COUNT(*) FROM resources`).Scan(&n); e != nil || n != 1 {
		t.Fatal("resource retention", n, e)
	}
	if e := a.Store.DB.QueryRow(`SELECT COUNT(*) FROM polls`).Scan(&n); e != nil || n != 1 {
		t.Fatal("poll retention", n, e)
	}
}

func TestLiveWallstreet(t *testing.T) {
	if os.Getenv("FINEVENT_LIVE_TEST") != "1" {
		t.Skip("explicit opt-in for a read-only public-source check")
	}
	client := &http.Client{Timeout: 15 * time.Second}
	items, e := FetchWallstreet(context.Background(), client, WallstreetURL)
	if e != nil {
		t.Fatal(e)
	}
	t.Logf("source items=%d, newest published UTC=%s", len(items), time.Unix(items[0].PublishedAt, 0).UTC().Format(time.RFC3339))
}
