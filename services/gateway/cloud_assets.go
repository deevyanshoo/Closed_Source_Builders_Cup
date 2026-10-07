package main

import (
	"errors"
	"io"
	"mime"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strings"
)

type assetsHandler struct{ root string }

func newAssetsHandler(root string) (http.Handler, error) {
	clean, err := filepath.EvalSymlinks(root)
	if err != nil {
		return nil, errors.New("assets unavailable")
	}
	info, err := os.Stat(clean)
	if err != nil || !info.IsDir() {
		return nil, errors.New("assets unavailable")
	}
	index, err := os.Lstat(filepath.Join(clean, "index.html"))
	if err != nil || !index.Mode().IsRegular() {
		return nil, errors.New("assets unavailable")
	}
	h := assetsHandler{root: clean}
	if !h.regularInside(filepath.Join(clean, "index.html")) {
		return nil, errors.New("assets unavailable")
	}
	return h, nil
}

func (a assetsHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != "GET" && r.Method != "HEAD" {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	p := r.URL.Path
	if !strings.HasPrefix(p, "/") || strings.Contains(p, "\\") ||
		path.Clean(p) != p || strings.HasPrefix(p, "/v2/") ||
		p == "/v2" || strings.HasPrefix(p, "/api/") || p == "/api" ||
		p == "/health" || p == "/ready" {
		http.NotFound(w, r)
		return
	}
	relative := strings.TrimPrefix(p, "/")
	if relative == "" {
		relative = "index.html"
	}
	file := filepath.Join(a.root, filepath.FromSlash(relative))
	if !a.regularInside(file) {
		if path.Ext(relative) != "" || !strings.Contains(r.Header.Get("Accept"), "text/html") {
			http.NotFound(w, r)
			return
		}
		file = filepath.Join(a.root, "index.html")
		if !a.regularInside(file) {
			http.NotFound(w, r)
			return
		}
	}
	f, err := os.Open(file)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() {
		http.NotFound(w, r)
		return
	}
	kind := mime.TypeByExtension(filepath.Ext(file))
	if strings.HasSuffix(file, "index.html") {
		kind = "text/html; charset=utf-8"
	}
	if kind != "" {
		w.Header().Set("Content-Type", kind)
	}
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Method == "HEAD" {
		w.WriteHeader(200)
		return
	}
	_, _ = io.Copy(w, f)
}

func (a assetsHandler) regularInside(file string) bool {
	real, err := filepath.EvalSymlinks(file)
	if err != nil {
		return false
	}
	rel, err := filepath.Rel(a.root, real)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return false
	}
	info, err := os.Stat(real)
	return err == nil && info.Mode().IsRegular()
}
