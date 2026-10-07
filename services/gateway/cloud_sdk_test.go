package main

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"cloud.google.com/go/auth/credentials/idtoken"
)

func signedMachineToken(t *testing.T, key *rsa.PrivateKey, claims map[string]any) string {
	t.Helper()
	header, _ := json.Marshal(map[string]any{"alg": "RS256", "typ": "JWT", "kid": "test-key"})
	body, _ := json.Marshal(claims)
	input := base64.RawURLEncoding.EncodeToString(header) + "." + base64.RawURLEncoding.EncodeToString(body)
	digest := sha256.Sum256([]byte(input))
	signature, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, digest[:])
	if err != nil {
		t.Fatal(err)
	}
	return input + "." + base64.RawURLEncoding.EncodeToString(signature)
}

func TestGoogleSDKSignatureAndPrivateClaimPolicy(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	n := base64.RawURLEncoding.EncodeToString(key.PublicKey.N.Bytes())
	e := base64.RawURLEncoding.EncodeToString(big.NewInt(int64(key.PublicKey.E)).Bytes())
	certs := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"keys": []map[string]string{
			{"kid": "test-key", "kty": "RSA", "alg": "RS256", "use": "sig", "n": n, "e": e},
		}})
	}))
	defer certs.Close()
	validator, err := idtoken.NewValidator(&idtoken.ValidatorOptions{
		Client: certs.Client(), RS256CertsURL: certs.URL,
	})
	if err != nil {
		t.Fatal(err)
	}
	cfg := privateFixture(t)
	cfg.Verifier = googleMachineVerifier{validator: validator}
	h, err := newPrivateStateHandler(cfg)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().Unix()
	base := map[string]any{
		"iss": "https://accounts.google.com", "aud": "https://gateway.example.test",
		"sub": "subject-a", "email": "runner@project.iam.gserviceaccount.com",
		"email_verified": true, "iat": now - 10, "exp": now + 3600,
	}
	valid := signedMachineToken(t, key, base)
	if got := privateCall(h, "GET", "/v2/session", valid).Code; got != 200 {
		t.Fatalf("valid signed token %d", got)
	}
	mutations := map[string]any{
		"iss": "https://evil.example", "aud": "https://other.example",
		"sub": "subject-b", "email": "other@project.iam.gserviceaccount.com",
		"email_verified": false, "iat": now + 3600, "exp": now - 1,
	}
	for name, value := range mutations {
		claims := map[string]any{}
		for k, v := range base {
			claims[k] = v
		}
		claims[name] = value
		if got := privateCall(h, "GET", "/v2/session", signedMachineToken(t, key, claims)).Code; got != 401 {
			t.Errorf("accepted %s: %d", name, got)
		}
	}
	other, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	if got := privateCall(h, "GET", "/v2/session", signedMachineToken(t, other, base)).Code; got != 401 {
		t.Errorf("accepted wrong signature: %d", got)
	}
}
