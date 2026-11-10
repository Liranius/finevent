package probe

import (
	"context"
	"log"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"

	"finevent/internal/calendar"
)

func procValue(path, key string) int64 {
	b, err := os.ReadFile(path)
	if err != nil {
		return 0
	}
	for _, line := range strings.Split(string(b), "\n") {
		f := strings.Fields(line)
		if len(f) >= 2 && strings.TrimSuffix(f[0], ":") == key {
			v, _ := strconv.ParseInt(f[1], 10, 64)
			if len(f) > 2 && f[2] == "kB" {
				v *= 1024
			}
			return v
		}
	}
	return 0
}
func fileNumber(path string) int64 {
	b, e := os.ReadFile(path)
	if e != nil {
		return 0
	}
	v, _ := strconv.ParseInt(strings.TrimSpace(string(b)), 10, 64)
	return v
}

func (a *App) SampleResources() error {
	var mem runtime.MemStats
	runtime.ReadMemStats(&mem)
	cgroup := "/sys/fs/cgroup"
	if b, e := os.ReadFile("/proc/self/cgroup"); e == nil {
		for _, line := range strings.Split(string(b), "\n") {
			if strings.HasPrefix(line, "0::") {
				cgroup += strings.TrimPrefix(line, "0::")
				break
			}
		}
	}
	_, err := a.Store.DB.Exec(`INSERT OR REPLACE INTO resources VALUES(?,?,?,?,?,?,?)`, time.Now().Unix(), procValue("/proc/self/status", "VmRSS"), mem.Sys, procValue("/proc/meminfo", "MemAvailable"), fileNumber(cgroup+"/memory.current"), fileNumber(cgroup+"/memory.peak"), procValue(cgroup+"/memory.events", "oom_kill"))
	return err
}

func (a *App) due(source string, interval time.Duration) bool {
	if source == "wallstreetcn" {
		return a.Store.wallstreetDue(time.Now(), interval)
	}
	var completed int64
	var ok bool
	err := a.Store.DB.QueryRow(`SELECT completed,success FROM polls WHERE source=? ORDER BY id DESC LIMIT 1`, source).Scan(&completed, &ok)
	if err != nil {
		return true
	}
	if !ok {
		interval = max(interval, 5*time.Minute)
	}
	return time.Now().Unix()-completed >= int64(interval.Seconds())
}

func (a *App) Run(ctx context.Context, collect bool, trend string) {
	defer a.aiProbeWorkers.Wait()
	aiDone := make(chan struct{})
	defer func() { <-aiDone }()
	go func() { defer close(aiDone); a.runAnalysis(ctx) }()
	newsDone := make(chan struct{})
	defer func() { <-newsDone }()
	go func() {
		defer close(newsDone)
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if _, err := a.Store.ProcessNewsBatch(ctx, time.Now().Unix()); err != nil && ctx.Err() == nil {
					log.Printf("news indexing: %v", err)
				}
			}
		}
	}()
	calendarDone := make(chan struct{})
	defer func() { <-calendarDone }()
	go func() {
		defer close(calendarDone)
		if !collect {
			return
		}
		s := &calendar.Store{DB: a.Store.DB}
		ticker := time.NewTicker(time.Minute)
		defer ticker.Stop()
		for {
			if err := s.SyncDue(ctx, a.CalendarClient); err != nil && ctx.Err() == nil {
				log.Printf("calendar sync: %v", err)
			}
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
			}
		}
	}()
	pushDone := make(chan struct{})
	defer func() { <-pushDone }()
	go func() {
		defer close(pushDone)
		lastReminder := time.Time{}
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if time.Since(lastReminder) >= 10*time.Second {
					if err := a.PlanNewsPush(time.Now().Unix()); err != nil {
						log.Printf("news push: %v", err)
					}
					if err := a.ReconcileReminders(time.Now().Unix()); err != nil {
						log.Printf("calendar reminders: %v", err)
					} else {
						lastReminder = time.Now()
					}
				}
				if err := a.SendDue(ctx); err != nil {
					log.Printf("delivery worker: %v", err)
				}
			}
		}
	}()
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	probeTicker := time.NewTicker(time.Second)
	defer probeTicker.Stop()
	lastSample := time.Time{}
	lastPrune := time.Time{}
	for {
		if time.Since(lastSample) >= time.Minute {
			if err := a.SampleResources(); err != nil {
				log.Printf("resource sample: %v", err)
			}
			lastSample = time.Now()
		}
		if time.Since(lastPrune) >= time.Hour {
			if err := a.Store.Prune(time.Now()); err != nil {
				log.Printf("retention: %v", err)
			}
			lastPrune = time.Now()
		}
		if collect && a.due("wallstreetcn", 2*time.Minute) {
			// An administrator-created, expiring request enables one bounded real-model probe.
			// Start alongside the normal collector so both use this service's cgroup.
			a.collectProbeSource(ctx, WallstreetURL, a.startAIProbe(ctx))
		}
		if collect && trend != "" && a.due("trendradar", time.Minute) {
			p := a.CollectTrend(trend)
			if err := a.Store.SavePoll(p); err != nil {
				log.Printf("save TrendRadar observation: %v", err)
			}
		}
	nextCycle:
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				break nextCycle
			case <-probeTicker.C:
				// A private, expiring one-shot request may align with an upstream job.
				// Ordinary collection retains its existing cadence; no extra fetch without a reservation.
				if collect {
					if id := a.startAIProbe(ctx); id != "" {
						a.collectProbeSource(ctx, WallstreetURL, id)
					}
				}
			}
		}
	}
}
