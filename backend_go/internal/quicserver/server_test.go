package quicserver

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/quic-go/quic-go"

	"ark_relay/internal/authjwt"
	"ark_relay/internal/hub"
	"ark_relay/internal/protocol"
	"ark_relay/internal/store"
	"ark_relay/internal/tokenauth"
)

type fakeGroupChecker struct {
	members      map[string]bool
	activeGroups map[string]string // accountID -> groupID
	err          error
}

func (f *fakeGroupChecker) IsMember(_ context.Context, groupID, accountID string) (bool, error) {
	if f.err != nil {
		return false, f.err
	}
	return f.members[groupID+"/"+accountID], nil
}

func (f *fakeGroupChecker) ActiveGroup(_ context.Context, accountID string) (string, error) {
	if f.err != nil {
		return "", f.err
	}
	groupID, ok := f.activeGroups[accountID]
	if !ok {
		return "", store.ErrNoActiveGroup
	}
	return groupID, nil
}

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
	claims := jwt.MapClaims{"account_id": accountID, "exp": time.Now().Add(time.Hour).Unix()}
	token := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	signed, err := token.SignedString(priv)
	if err != nil {
		t.Fatalf("sign token: %v", err)
	}
	return signed
}

// startTestServer boots a real quicserver.Server on loopback with a
// generated dev cert, returning its UDP address and a stop func. Real
// sockets, real TLS handshake, real QUIC streams — the DTO-sharing plan
// explicitly calls for arkmultitool's QUIC transport to be exercised for
// real, not just type-checked; this is that exercise for the server half.
func startTestServer(t *testing.T, h *hub.Hub, verifier *authjwt.Verifier, members *fakeGroupChecker) string {
	t.Helper()
	tlsConf, err := GenerateDevTLSConfig()
	if err != nil {
		t.Fatalf("generate dev tls config: %v", err)
	}
	// keys=nil: none of these tests exercise the api_key fallback path
	// (that's tokenauth's own test suite).
	resolver := tokenauth.New(verifier, nil)
	s := New(h, resolver, noopEntityWriter{}, noopHashCache{}, noopPublisher{}, members,
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		30*time.Second, 60*time.Second, 10*time.Second, 500, 64*1024)

	ln, err := quic.ListenAddr("127.0.0.1:0", tlsConf, &quic.Config{MaxIdleTimeout: 60 * time.Second})
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	addr := ln.Addr().String()

	ctx, cancel := context.WithCancel(context.Background())
	// serve (not the address-string ListenAndServe) so the listener is
	// already bound and accepting before dial ever runs — no
	// close-then-rebind window for a client to race against.
	go func() { _ = s.serve(ctx, ln) }()
	t.Cleanup(cancel)
	return addr
}

// dial opens a QUIC connection + one stream to addr, skipping cert
// verification (the dev cert is self-signed and freshly generated every
// test run — pinning it would be pointless busywork for a test).
func dial(t *testing.T, addr string) *quic.Stream {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn, err := quic.DialAddr(ctx, addr, &tls.Config{InsecureSkipVerify: true, NextProtos: []string{"ark-quic-v1"}}, nil)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { _ = conn.CloseWithError(0, "test done") })
	stream, err := conn.OpenStreamSync(ctx)
	if err != nil {
		t.Fatalf("open stream: %v", err)
	}
	return stream
}

func doHandshake(t *testing.T, stream *quic.Stream, token, serverIP string) handshakeResponse {
	t.Helper()
	req, err := json.Marshal(handshakeRequest{Token: token, ServerIP: serverIP})
	if err != nil {
		t.Fatalf("marshal handshake: %v", err)
	}
	if err := writeFrame(stream, req); err != nil {
		t.Fatalf("write handshake: %v", err)
	}
	_ = stream.SetReadDeadline(time.Now().Add(5 * time.Second))
	raw, err := readFrame(stream, maxHandshakeBytes)
	if err != nil {
		t.Fatalf("read handshake response: %v", err)
	}
	var resp handshakeResponse
	if err := json.Unmarshal(raw, &resp); err != nil {
		t.Fatalf("unmarshal handshake response: %v", err)
	}
	return resp
}

func TestHandshake_InvalidTokenRejected(t *testing.T) {
	verifier, _ := testVerifier(t)
	h := hub.New(slog.New(slog.NewTextHandler(io.Discard, nil)))
	addr := startTestServer(t, h, verifier, &fakeGroupChecker{})

	stream := dial(t, addr)
	resp := doHandshake(t, stream, "garbage-token", "1.2.3.4:7777")

	if resp.OK {
		t.Fatal("expected handshake to be rejected for an invalid token")
	}
}

func TestHandshake_NoActiveGroupRejected(t *testing.T) {
	verifier, priv := testVerifier(t)
	h := hub.New(slog.New(slog.NewTextHandler(io.Discard, nil)))
	// No activeGroups entry for acct-1 at all -- token resolves fine, but
	// there's no group_id on the wire to trust anymore and none resolved
	// server-side either.
	addr := startTestServer(t, h, verifier, &fakeGroupChecker{})

	stream := dial(t, addr)
	token := signToken(t, priv, "acct-1")
	resp := doHandshake(t, stream, token, "1.2.3.4:7777")

	if resp.OK {
		t.Fatal("expected handshake to be rejected for an account with no active group")
	}
}

func TestHandshake_NotAMemberRejected(t *testing.T) {
	verifier, priv := testVerifier(t)
	h := hub.New(slog.New(slog.NewTextHandler(io.Discard, nil)))
	// Active group resolves to g1, but the membership set says acct-1
	// isn't actually in it (out of sync, e.g. mid-leave).
	addr := startTestServer(t, h, verifier, &fakeGroupChecker{
		activeGroups: map[string]string{"acct-1": "g1"},
		members:      map[string]bool{},
	})

	stream := dial(t, addr)
	token := signToken(t, priv, "acct-1")
	resp := doHandshake(t, stream, token, "1.2.3.4:7777")

	if resp.OK {
		t.Fatal("expected handshake to be rejected for a non-member")
	}
}

// Пустой токен отбивается до резолва, на той же проверке обязательных
// полей, что и пустой server_ip -- отдельным тестом, потому что это
// единственный отказ авторизации, который раньше покрывался только со
// стороны удалённого WS-транспорта (его ServeHTTP отвечал 401 на
// отсутствующий Bearer).
func TestHandshake_EmptyTokenRejected(t *testing.T) {
	verifier, _ := testVerifier(t)
	h := hub.New(slog.New(slog.NewTextHandler(io.Discard, nil)))
	addr := startTestServer(t, h, verifier, &fakeGroupChecker{
		activeGroups: map[string]string{"acct-1": "g1"},
		members:      map[string]bool{"g1/acct-1": true},
	})

	stream := dial(t, addr)
	resp := doHandshake(t, stream, "", "1.2.3.4:7777") // token missing

	if resp.OK {
		t.Fatal("expected handshake to be rejected for an empty token")
	}
}

func TestHandshake_MissingServerIPRejected(t *testing.T) {
	verifier, priv := testVerifier(t)
	h := hub.New(slog.New(slog.NewTextHandler(io.Discard, nil)))
	addr := startTestServer(t, h, verifier, &fakeGroupChecker{
		activeGroups: map[string]string{"acct-1": "g1"},
		members:      map[string]bool{"g1/acct-1": true},
	})

	stream := dial(t, addr)
	token := signToken(t, priv, "acct-1")
	resp := doHandshake(t, stream, token, "") // server_ip missing

	if resp.OK {
		t.Fatal("expected handshake to be rejected for a missing server_ip")
	}
}

// TestEndToEnd_SightingBroadcastsToOtherQUICClient exercises the whole
// path for real: two QUIC clients authenticate into the same
// (group_id, server_ip) room, one sends a sighting batch, and the other
// receives it over the wire.
func TestEndToEnd_SightingBroadcastsToOtherQUICClient(t *testing.T) {
	verifier, priv := testVerifier(t)
	h := hub.New(slog.New(slog.NewTextHandler(io.Discard, nil)))
	members := &fakeGroupChecker{
		activeGroups: map[string]string{"acct-1": "g1", "acct-2": "g1"},
		members: map[string]bool{
			"g1/acct-1": true,
			"g1/acct-2": true,
		},
	}
	addr := startTestServer(t, h, verifier, members)

	senderStream := dial(t, addr)
	if resp := doHandshake(t, senderStream, signToken(t, priv, "acct-1"), "10.0.0.1:7777"); !resp.OK {
		t.Fatalf("sender handshake rejected: %s", resp.Error)
	}

	watcherStream := dial(t, addr)
	if resp := doHandshake(t, watcherStream, signToken(t, priv, "acct-2"), "10.0.0.1:7777"); !resp.OK {
		t.Fatalf("watcher handshake rejected: %s", resp.Error)
	}

	// Give the hub a moment to finish registering both clients before the
	// sighting is sent — Register happens on Client.Run's goroutine, which
	// starts asynchronously right after the handshake ack above.
	time.Sleep(100 * time.Millisecond)

	inbound := protocol.Inbound{
		Type: protocol.MsgSighting,
		Seq:  1,
		Entities: []protocol.Entity{
			{Cat: protocol.CategoryPlayer, Key: "player:steve:42", Label: "Steve", Team: 42},
		},
	}
	payload, err := json.Marshal(inbound)
	if err != nil {
		t.Fatalf("encode inbound: %v", err)
	}
	if err := writeFrame(senderStream, payload); err != nil {
		t.Fatalf("write sighting: %v", err)
	}

	_ = watcherStream.SetReadDeadline(time.Now().Add(5 * time.Second))
	raw, err := readFrame(watcherStream, 64*1024)
	if err != nil {
		t.Fatalf("watcher did not receive the broadcast sighting: %v", err)
	}
	out, err := protocol.Decode(raw)
	if err != nil {
		t.Fatalf("decode broadcast: %v", err)
	}
	if out.Type != protocol.MsgSighting || len(out.Entities) != 1 || out.Entities[0].Label != "Steve" {
		t.Fatalf("unexpected broadcast payload: %+v", out)
	}
}
