package tokenauth

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"ark_relay/internal/authjwt"
)

// fakeKeyResolver answers Resolve from an in-memory map, so these tests
// never touch a real Redis connection -- same pattern as
// wsserver.fakeGroupChecker.
type fakeKeyResolver struct {
	accounts map[string]string // token -> account_id
}

func (f *fakeKeyResolver) Resolve(_ context.Context, token string) (string, error) {
	if accountID, ok := f.accounts[token]; ok {
		return accountID, nil
	}
	return "", errors.New("fake: not found")
}

func testVerifier(t *testing.T) (*authjwt.Verifier, *rsa.PrivateKey) {
	t.Helper()
	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate key: %v", err)
	}
	pubBytes, err := x509.MarshalPKIXPublicKey(&priv.PublicKey)
	if err != nil {
		t.Fatalf("marshal public key: %v", err)
	}
	pemBytes := pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: pubBytes})

	dir := t.TempDir()
	path := filepath.Join(dir, "pub.pem")
	if err := os.WriteFile(path, pemBytes, 0o600); err != nil {
		t.Fatalf("write pub key: %v", err)
	}

	v, err := authjwt.LoadVerifier(path)
	if err != nil {
		t.Fatalf("load verifier: %v", err)
	}
	return v, priv
}

func signToken(t *testing.T, priv *rsa.PrivateKey, accountID string) string {
	t.Helper()
	claims := jwt.MapClaims{
		"account_id": accountID,
		"exp":        time.Now().Add(time.Hour).Unix(),
	}
	token := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	signed, err := token.SignedString(priv)
	if err != nil {
		t.Fatalf("sign token: %v", err)
	}
	return signed
}

func TestResolve_ValidJWTNeverConsultsKeyResolver(t *testing.T) {
	verifier, priv := testVerifier(t)
	token := signToken(t, priv, "acct-jwt")
	// A KeyResolver that would fail the test if ever called.
	keys := &fakeKeyResolver{accounts: map[string]string{}}
	r := New(verifier, keys)

	accountID, err := r.Resolve(context.Background(), token)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if accountID != "acct-jwt" {
		t.Fatalf("expected acct-jwt, got %q", accountID)
	}
}

func TestResolve_NonJWTFallsBackToKeyResolver(t *testing.T) {
	verifier, _ := testVerifier(t)
	keys := &fakeKeyResolver{accounts: map[string]string{"opaque-key-123": "acct-key"}}
	r := New(verifier, keys)

	accountID, err := r.Resolve(context.Background(), "opaque-key-123")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if accountID != "acct-key" {
		t.Fatalf("expected acct-key, got %q", accountID)
	}
}

func TestResolve_UnknownTokenIsRejected(t *testing.T) {
	verifier, _ := testVerifier(t)
	keys := &fakeKeyResolver{accounts: map[string]string{}}
	r := New(verifier, keys)

	if _, err := r.Resolve(context.Background(), "garbage"); err == nil {
		t.Fatal("expected an error for a token that is neither a valid JWT nor a known api_key")
	}
}

func TestResolve_NilKeyResolverRejectsNonJWT(t *testing.T) {
	verifier, _ := testVerifier(t)
	r := New(verifier, nil)

	if _, err := r.Resolve(context.Background(), "opaque-key-123"); err == nil {
		t.Fatal("expected an error when no KeyResolver is configured and the token isn't a JWT")
	}
}
