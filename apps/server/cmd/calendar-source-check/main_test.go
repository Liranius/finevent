package main

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestCandidateUsesBoundedOfficialTargetsWithoutRetryingBLS(t *testing.T) {
	selected, timeout, mode := diagnosticTargets(true)
	if len(selected) != 3 || timeout != time.Minute || mode != "candidate" {
		t.Fatal(selected, timeout, mode)
	}
	for _, item := range selected {
		u, err := url.Parse(item.URL)
		if err != nil || u.Scheme != "https" || (u.Host != "www.stats.gov.cn" && u.Host != "www.newyorkfed.org") {
			t.Fatal("unexpected candidate request", item)
		}
	}
	baseline, timeout, mode := diagnosticTargets(false)
	if len(baseline) != 4 || timeout != 15*time.Second || mode != "deployed_baseline" {
		t.Fatal("baseline changed", baseline, timeout, mode)
	}
}

type fakeTransport func(*http.Request) (*http.Response, error)

func (f fakeTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type brokenBody struct{ io.Reader }

func (b brokenBody) Read(p []byte) (int, error) {
	n, err := b.Reader.Read(p)
	if errors.Is(err, io.EOF) {
		return n, io.ErrUnexpectedEOF
	}
	return n, err
}
func (brokenBody) Close() error { return nil }

func TestReadFailureHasPartialByteCountWithoutBodyContent(t *testing.T) {
	tr := fakeTransport(func(req *http.Request) (*http.Response, error) {
		if req.Header.Get("User-Agent") != "finevent/0.2 (personal financial calendar)" {
			t.Fatal("request differs from production")
		}
		return &http.Response{StatusCode: 200, Proto: "HTTP/1.1", ContentLength: 100,
			Body: brokenBody{strings.NewReader("partial")}}, nil
	})
	r := check(context.Background(), target{"nbs", "https://www.stats.gov.cn/", ""}, tr, time.Second)
	if r.ReadErrorKind != "unexpected_eof" || r.BodyBytes != 7 || r.FetchError == "" || len(r.BodySHA256) != 64 {
		t.Fatal(r)
	}
}

func TestForbiddenDoesNotRetryOrParse(t *testing.T) {
	calls := 0
	tr := fakeTransport(func(req *http.Request) (*http.Response, error) {
		calls++
		return &http.Response{StatusCode: 403, Body: io.NopCloser(strings.NewReader("private diagnostic"))}, nil
	})
	r := check(context.Background(), target{"bls", "https://www.bls.gov/", "bls"}, tr, time.Second)
	if calls != 1 || r.Status != 403 || r.BodyBytes != 0 || r.FetchError == "" || r.ParsedEvents != 0 {
		t.Fatal(r, calls)
	}
}

func TestRequestErrorDoesNotExportUnderlyingMessage(t *testing.T) {
	tr := fakeTransport(func(req *http.Request) (*http.Response, error) {
		return nil, errors.New("private proxy URL")
	})
	r := check(context.Background(), target{"test", "https://www.bls.gov/", ""}, tr, time.Second)
	if r.RequestErrorKind != "*errors.errorString" || strings.Contains(r.FetchError, "private") {
		t.Fatal(r)
	}
	if errorKind(context.DeadlineExceeded) != "deadline_exceeded" || errorKind(io.EOF) != "" {
		t.Fatal("incorrect timeout or normal EOF classification")
	}
}
