package calendar

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestRolloverRepairPreservesFailureAndClaimsOnlyOnce(t *testing.T) {
	s := testStore(t)
	previous := Source{ID: "nyfed", LastAttempt: time.Now().Unix(), Error: "纽约联储当前页月份未更新，保留已有日程"}
	if err := s.Import(previous.ID, nil, previous.LastAttempt, errors.New(previous.Error)); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		claimed, err := s.claimRolloverRetry(previous)
		if err != nil || claimed != (i == 0) {
			t.Fatal(claimed, err)
		}
		if err = Init(s.DB); err != nil {
			t.Fatal(err)
		}
	}
	var raw, lastError string
	var lastAttempt, success int64
	if err := s.DB.QueryRow(`SELECT previous_state FROM calendar_source_repairs`).Scan(&raw); err != nil {
		t.Fatal(err)
	}
	var evidence Source
	if err := json.Unmarshal([]byte(raw), &evidence); err != nil {
		t.Fatal(err)
	}
	if evidence.ID != previous.ID || evidence.Error != previous.Error || evidence.LastAttempt != previous.LastAttempt {
		t.Fatal("original failure evidence lost", raw)
	}
	if err := s.DB.QueryRow(`SELECT last_attempt,last_success,error FROM calendar_sources WHERE id='nyfed'`).Scan(&lastAttempt, &success, &lastError); err != nil || lastAttempt != previous.LastAttempt || success != 0 || lastError != previous.Error {
		t.Fatal("claim changed source state", err)
	}
}

func TestRolloverRepairIgnoresOtherFailuresAndChangedSourceState(t *testing.T) {
	base := Source{ID: "nyfed", LastAttempt: 100, Error: "纽约联储当前页月份未更新，保留已有日程"}
	for _, kind := range []string{"other_source", "other_error", "successful_source", "nonempty_source", "stale_cursor"} {
		t.Run(kind, func(t *testing.T) {
			s := testStore(t)
			input := base
			if kind == "other_source" {
				input.ID = "bls"
			}
			if kind == "other_error" {
				input.Error = "HTTP 503"
			}
			if kind == "successful_source" {
				input.LastSuccess = 99
			}
			if kind == "nonempty_source" {
				input.Count = 1
			}
			if err := s.Import(input.ID, nil, input.LastAttempt, errors.New(input.Error)); err != nil {
				t.Fatal(err)
			}
			if kind == "stale_cursor" {
				input.LastAttempt--
			}
			claimed, err := s.claimRolloverRetry(input)
			if err != nil || claimed {
				t.Fatal(claimed, err)
			}
		})
	}
}

func TestSyncDueRetriesRepairedSourceWithoutWaitingSixHours(t *testing.T) {
	s := testStore(t)
	now := time.Now().Unix()
	for _, source := range Sources() {
		failure := "not due"
		if source.ID == "nyfed" {
			failure = "纽约联储当前页月份未更新，保留已有日程"
		}
		if err := s.Import(source.ID, nil, now, errors.New(failure)); err != nil {
			t.Fatal(err)
		}
	}
	calls := 0
	client := NewHTTPClient()
	client.Transport = transport(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.URL.String() != nyfedURL {
			t.Fatal("unrelated source retried", r.URL)
		}
		return &http.Response{StatusCode: 503, Body: io.NopCloser(strings.NewReader(""))}, nil
	})
	for i := 0; i < 2; i++ {
		if err := s.SyncDue(context.Background(), client); err != nil {
			t.Fatal(err)
		}
	}
	if calls != 1 {
		t.Fatal("retry must be immediate and single", calls)
	}
}
