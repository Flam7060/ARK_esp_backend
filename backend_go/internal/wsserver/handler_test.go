package wsserver

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"ark_relay/internal/authjwt"
	"ark_relay/internal/hub"
	"ark_relay/internal/store"
)

// fakeGroupChecker answers IsMember from an in-memory set, so these tests
// never touch a real Redis connection.
type fakeGroupChecker struct {
	members map[string]bool // "groupID/accountID" -> member
	err     error
}

func (f *fakeGroupChecker) IsMember(_ context.Context, groupID, accountID string) (bool, error) {
	if f.err != nil {
		return false, f.err
	}
	return f.members[groupID+"/"+accountID], nil
}

// noop* below satisfy just enough of hub.EntityWriter/hub.HashCache/
// streamproducer.Publisher to build a Handler -- every test here is
// rejected before reaching a Client at all (auth/membership gates), so
// these are never actually called; New just needs concrete arguments.
type noopEntityWriter struct{}

func (noopEntityWriter) Upsert(context.Context, store.EntityWrite) error { return nil }

type noopHashCache struct{}

func (noopHashCache) LastHash(context.Context, string, string, string) (string, time.Time, error) {
	return "", time.Time{}, nil
}

func (noopHashCache) SetLastHash(context.Context, string, string, string, string, time.Time) error {
	return nil
}

type noopPublisher struct{}

func (noopPublisher) Publish(context.Context, string, map[string]string) error { return nil }

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

func newTestHandler(t *testing.T, verifier *authjwt.Verifier, members *fakeGroupChecker) *Handler {
	t.Helper()
	h := hub.New(slog.New(slog.NewTextHandler(io.Discard, nil)))
	// Upsert/hash-cache/publisher are never reached by the request paths
	// under test (all rejected before Upgrade), so nil-ish no-op stand-ins
	// are enough -- New only stores them, it doesn't call them itself.
	return New(h, verifier, noopEntityWriter{}, noopHashCache{}, noopPublisher{}, members,
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		30*time.Second, 60*time.Second, 10*time.Second, 500, 64*1024)
}

func TestServeHTTP_MissingToken(t *testing.T) {
	verifier, _ := testVerifier(t)
	h := newTestHandler(t, verifier, &fakeGroupChecker{})

	req := httptest.NewRequest(http.MethodGet, "/v1/relay/ws?group_id=g1&server_ip=1.2.3.4:7777", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 for missing token, got %d", rec.Code)
	}
}

func TestServeHTTP_InvalidToken(t *testing.T) {
	verifier, _ := testVerifier(t)
	h := newTestHandler(t, verifier, &fakeGroupChecker{})

	req := httptest.NewRequest(http.MethodGet, "/v1/relay/ws?group_id=g1&server_ip=1.2.3.4:7777&token=garbage", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 for an unparsable token, got %d", rec.Code)
	}
}

func TestServeHTTP_MissingGroupOrServerParams(t *testing.T) {
	verifier, priv := testVerifier(t)
	h := newTestHandler(t, verifier, &fakeGroupChecker{})
	token := signToken(t, priv, "acct-1")

	req := httptest.NewRequest(http.MethodGet, "/v1/relay/ws?token="+token, nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for missing group_id/server_ip, got %d", rec.Code)
	}
}

func TestServeHTTP_NotAGroupMemberIsRefused(t *testing.T) {
	verifier, priv := testVerifier(t)
	members := &fakeGroupChecker{members: map[string]bool{}} // acct-1 is not in g1
	h := newTestHandler(t, verifier, members)
	token := signToken(t, priv, "acct-1")

	req := httptest.NewRequest(http.MethodGet, "/v1/relay/ws?group_id=g1&server_ip=1.2.3.4:7777&token="+token, nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for a non-member connect attempt, got %d", rec.Code)
	}
}

func TestServeHTTP_MembershipCheckErrorFailsClosed(t *testing.T) {
	verifier, priv := testVerifier(t)
	members := &fakeGroupChecker{err: context.DeadlineExceeded}
	h := newTestHandler(t, verifier, members)
	token := signToken(t, priv, "acct-1")

	req := httptest.NewRequest(http.MethodGet, "/v1/relay/ws?group_id=g1&server_ip=1.2.3.4:7777&token="+token, nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("expected a failed membership check to refuse the connection (fail closed), got %d", rec.Code)
	}
}

func TestServeHTTP_MemberPassesAuthAndReachesUpgrade(t *testing.T) {
	verifier, priv := testVerifier(t)
	members := &fakeGroupChecker{members: map[string]bool{"g1/acct-1": true}}
	h := newTestHandler(t, verifier, members)
	token := signToken(t, priv, "acct-1")

	req := httptest.NewRequest(http.MethodGet, "/v1/relay/ws?group_id=g1&server_ip=1.2.3.4:7777&token="+token, nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	// This plain httptest.Recorder request has no real WebSocket upgrade
	// headers, so gorilla's Upgrader itself rejects it with 400 once
	// control reaches that point -- that 400 is expected and proves this
	// request got PAST every auth/membership gate (which reject with
	// 401/403/500, never letting the request reach Upgrade at all). What
	// this test actually proves is that a legitimate member clears every
	// gate; the real handshake is exercised by the docker-compose
	// integration check in the DTO-sharing plan, not at this level.
	if rec.Code == http.StatusUnauthorized || rec.Code == http.StatusForbidden ||
		rec.Code == http.StatusInternalServerError {
		t.Fatalf("expected a valid member to pass every auth/membership gate, got %d", rec.Code)
	}
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected gorilla's Upgrader to reject this non-websocket test request with 400, got %d -- "+
			"if this changed, the gate-bypass assumption above may no longer hold", rec.Code)
	}
	// Distinguish "reached Upgrade and got rejected there" from "never
	// left this handler's own param-validation gate" (both happen to
	// answer 400): this handler's own gate writes a specific message
	// ("group_id and server_ip query parameters are required"); gorilla's
	// Upgrader, with no custom u.Error callback configured, falls back to
	// the generic http.StatusText(400) body ("Bad Request") -- see
	// (*Upgrader).returnError. Seeing the generic text here proves this
	// request cleared this handler's own gate and failed inside Upgrade.
	if body := rec.Body.String(); strings.Contains(body, "group_id") {
		t.Fatalf("expected to clear this handler's own param gate, but got its rejection body: %q", body)
	}
}
