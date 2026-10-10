package main

import (
	"context"
	"crypto/sha256"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

var errQuoteLocalUnavailable = errors.New("local quote connector unavailable")

type quoteKeySeal struct {
	path       string
	dev, inode uint64
	mode       os.FileMode
	size       int64
	digest     [32]byte
}

func quoteLocalOrigin(raw string) (string, bool) {
	if !localOrigin(raw) {
		return "", false
	}
	u, err := url.Parse(raw)
	if err != nil || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || u.ForceQuery {
		return "", false
	}
	host, port, err := net.SplitHostPort(u.Host)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() || net.JoinHostPort(host, port) != u.Host || port == "" {
		return "", false
	}
	for _, digit := range port {
		if digit < '0' || digit > '9' {
			return "", false
		}
	}
	n, err := strconv.Atoi(port)
	if err != nil || n < 1 || n > 65535 {
		return "", false
	}
	return u.Host, true
}

func withQuoteLocalFlags(cfg stateConfig, args []string) (stateConfig, []string, error) {
	remaining := make([]string, 0, len(args))
	seen := map[string]bool{}
	for i := 0; i < len(args); i++ {
		name, value, inline := strings.Cut(args[i], "=")
		if name != "-quote-local-key-file" && name != "-quote-local-upstream" {
			remaining = append(remaining, args[i])
			continue
		}
		if seen[name] {
			return cfg, nil, errQuoteLocalUnavailable
		}
		seen[name] = true
		if !inline {
			i++
			if i >= len(args) {
				return cfg, nil, errQuoteLocalUnavailable
			}
			value = args[i]
		}
		if value == "" || strings.HasPrefix(value, "-") || strings.ContainsRune(value, 0) {
			return cfg, nil, errQuoteLocalUnavailable
		}
		if name == "-quote-local-key-file" {
			if cfg.QuoteLocalKeyFile != "" || !filepath.IsAbs(value) {
				return cfg, nil, errQuoteLocalUnavailable
			}
			cfg.QuoteLocalKeyFile = value
		} else {
			if cfg.QuoteLocalUpstream != "" {
				return cfg, nil, errQuoteLocalUnavailable
			}
			if _, ok := quoteLocalOrigin(value); !ok {
				return cfg, nil, errQuoteLocalUnavailable
			}
			cfg.QuoteLocalUpstream = value
		}
	}
	if cfg.QuoteLocalKeyFile != "" || cfg.QuoteLocalUpstream != "" {
		if cfg.Mode == "private_machine" || (cfg.Mode != "" && cfg.Mode != "local") || cfg.QuoteLocalKeyFile == "" || cfg.QuoteLocalUpstream == "" || !filepath.IsAbs(cfg.QuoteLocalKeyFile) {
			return cfg, nil, errQuoteLocalUnavailable
		}
		if _, ok := quoteLocalOrigin(cfg.QuoteLocalUpstream); !ok {
			return cfg, nil, errQuoteLocalUnavailable
		}
	}
	return cfg, remaining, nil
}

func quoteFileIdentity(info os.FileInfo) (uint64, uint64, bool) {
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || info.Mode().Perm() != 0600 || st.Uid != uint32(os.Getuid()) || st.Nlink != 1 || info.Size() < 32 || info.Size() > 4096 {
		return 0, 0, false
	}
	return uint64(st.Dev), uint64(st.Ino), true
}

func readQuoteKey(path string) (*quoteKeySeal, []byte, error) {
	if !filepath.IsAbs(path) || strings.ContainsRune(path, 0) {
		return nil, nil, errQuoteLocalUnavailable
	}
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NONBLOCK|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, nil, errQuoteLocalUnavailable
	}
	file := os.NewFile(uintptr(fd), "quote-key")
	defer file.Close()
	before, err := file.Stat()
	if err != nil {
		return nil, nil, errQuoteLocalUnavailable
	}
	dev, inode, ok := quoteFileIdentity(before)
	if !ok {
		return nil, nil, errQuoteLocalUnavailable
	}
	data, err := io.ReadAll(io.LimitReader(file, 4097))
	if err != nil || len(data) != int(before.Size()) || len(data) < 32 || len(data) > 4096 {
		return nil, nil, errQuoteLocalUnavailable
	}
	after, err := file.Stat()
	if err != nil || after.Mode() != before.Mode() || after.Size() != before.Size() {
		return nil, nil, errQuoteLocalUnavailable
	}
	devAfter, inodeAfter, ok := quoteFileIdentity(after)
	if !ok || devAfter != dev || inodeAfter != inode {
		return nil, nil, errQuoteLocalUnavailable
	}
	pathInfo, err := os.Lstat(path)
	if err != nil || !os.SameFile(after, pathInfo) || pathInfo.Mode() != after.Mode() {
		return nil, nil, errQuoteLocalUnavailable
	}
	seal := &quoteKeySeal{path: path, dev: dev, inode: inode, mode: after.Mode(), size: after.Size(), digest: sha256.Sum256(data)}
	return seal, data, nil
}

func (s *quoteKeySeal) valid() bool {
	if s == nil {
		return true // Only explicitly test-injected adapters omit a seal.
	}
	latest, key, err := readQuoteKey(s.path)
	if err != nil {
		return false
	}
	return latest.dev == s.dev && latest.inode == s.inode && latest.mode == s.mode && latest.size == s.size && latest.digest == s.digest && len(key) == int(s.size)
}

type quoteSealedTransport struct {
	origin string
	seal   *quoteKeySeal
	next   http.RoundTripper
}

func (t *quoteSealedTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	if t == nil || t.seal == nil || !t.seal.valid() || t.next == nil || r == nil || r.URL == nil || r.URL.Scheme != "http" || "http://"+r.URL.Host != t.origin || r.URL.User != nil {
		return nil, errQuoteLocalUnavailable
	}
	return t.next.RoundTrip(r)
}

func newQuoteLocalClient(origin string, seal *quoteKeySeal, next http.RoundTripper) *http.Client {
	address, ok := quoteLocalOrigin(origin)
	if !ok || seal == nil {
		return nil
	}
	if next == nil {
		dialer := &net.Dialer{Timeout: 3 * time.Second}
		next = &http.Transport{Proxy: nil, DialContext: func(ctx context.Context, network, requested string) (net.Conn, error) {
			if network != "tcp" || requested != address {
				return nil, errQuoteLocalUnavailable
			}
			return dialer.DialContext(ctx, "tcp", address)
		}}
	}
	return &http.Client{Timeout: 8 * time.Second, Transport: &quoteSealedTransport{origin: origin, seal: seal, next: next}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
}

func initQuoteHTTP(cfg stateConfig) *quoteAdapter {
	if cfg.Mode == "private_machine" || (cfg.Mode != "" && cfg.Mode != "local") || cfg.QuoteLocalKeyFile == "" || cfg.QuoteLocalUpstream == "" {
		return nil
	}
	if _, ok := quoteLocalOrigin(cfg.QuoteLocalUpstream); !ok {
		return nil
	}
	seal, key, err := readQuoteKey(cfg.QuoteLocalKeyFile)
	if err != nil {
		return nil
	}
	client := newQuoteLocalClient(cfg.QuoteLocalUpstream, seal, nil)
	if client == nil {
		return nil
	}
	return &quoteAdapter{upstream: cfg.QuoteLocalUpstream, key: key, client: client, seal: seal}
}
