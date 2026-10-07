package main

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"time"
)

type outboundTokenSource interface {
	Token(context.Context) (string, error)
}

type authenticatedTransport struct {
	origin string
	source outboundTokenSource
	base   http.RoundTripper
}

func (a authenticatedTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	if r.URL == nil || r.URL.Scheme != "https" ||
		r.URL.Scheme+"://"+r.URL.Host != a.origin || a.source == nil {
		return nil, errors.New("upstream origin unavailable")
	}
	token, err := a.source.Token(r.Context())
	if err != nil || token == "" || strings.ContainsAny(token, " \t\r\n") {
		return nil, errors.New("upstream identity unavailable")
	}
	out := r.Clone(r.Context())
	out.Header = r.Header.Clone()
	out.Header.Del("Authorization")
	out.Header.Del("X-Serverless-Authorization")
	out.Header.Set("Authorization", "Bearer "+token)
	out.Header.Set("X-Serverless-Authorization", "Bearer "+token)
	return a.base.RoundTrip(out)
}

func newPrivateOutboundClient(origin string, source outboundTokenSource) (*http.Client, error) {
	if !serviceOrigin(origin, "https") || source == nil {
		return nil, errors.New("invalid upstream transport")
	}
	return &http.Client{
		Timeout:       8 * time.Second,
		Transport:     authenticatedTransport{origin: origin, source: source, base: http.DefaultTransport},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}, nil
}
