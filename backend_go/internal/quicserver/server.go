// Package quicserver is the QUIC transport for ark_relay's sighting
// protocol — the server-side counterpart to arkmultitool's Http3Publisher
// (DTO-sharing plan: "real transport explicitly deferred to a future
// QUIC/HTTP-3 implementation"). It carries exactly the same
// protocol.Inbound/Outbound JSON messages as internal/wsserver's
// WebSocket path, over a length-prefixed QUIC stream instead of WS frames
// — hub.Client's dedup/broadcast/revocation logic is shared unchanged
// between both transports (see internal/hub.Conn).
//
// This is bare QUIC (github.com/quic-go/quic-go), not HTTP/3: a sighting
// connection is a long-lived, bidirectional, low-overhead stream of small
// JSON batches — the request/response shape HTTP/3 is built around (one
// stream per request, server push, header compression) buys nothing here
// and would only add a framing layer on top of a framing layer.
package quicserver

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"log/slog"
	"time"

	"github.com/quic-go/quic-go"

	"ark_relay/internal/hub"
	"ark_relay/internal/streamproducer"
	"ark_relay/internal/tokenauth"
)

// GroupChecker mirrors wsserver.GroupChecker — kept as its own interface
// (not imported from wsserver) so quicserver has no import-time dependency
// on the WS transport package; both are peers under cmd/relay, neither
// should need the other.
type GroupChecker interface {
	IsMember(ctx context.Context, groupID, accountID string) (bool, error)
}

// Server accepts QUIC connections and hands each one's single stream to
// the Hub after the same auth/authorization checks wsserver.Handler
// applies. Field-for-field mirror of wsserver.Handler by design — same
// checks, different transport.
type Server struct {
	hub      *hub.Hub
	resolver *tokenauth.Resolver
	store    hub.EntityWriter
	hashes   hub.HashCache
	stream   streamproducer.Publisher
	members  GroupChecker
	log      *slog.Logger

	pingInterval time.Duration
	pongWait     time.Duration
	writeWait    time.Duration
	maxEntities  int
	maxBytes     int64
}

// New builds a Server. Deadlines/limits are the same config values passed
// to wsserver.New — one set of tuning knobs for both transports, not two
// to keep in sync by hand.
func New(h *hub.Hub, resolver *tokenauth.Resolver, es hub.EntityWriter, hc hub.HashCache, sp streamproducer.Publisher,
	members GroupChecker, log *slog.Logger,
	pingInterval, pongWait, writeWait time.Duration, maxEntities int, maxBytes int64,
) *Server {
	return &Server{
		hub: h, resolver: resolver, store: es, hashes: hc, stream: sp, members: members, log: log,
		pingInterval: pingInterval, pongWait: pongWait, writeWait: writeWait,
		maxEntities: maxEntities, maxBytes: maxBytes,
	}
}

// ListenAndServe binds addr (UDP) with tlsConf — QUIC mandates TLS 1.3, so
// unlike the WS listener there is no plaintext option — and blocks
// accepting connections until ctx is cancelled. Each accepted connection is
// served in its own goroutine and never blocks Accept for the next one.
func (s *Server) ListenAndServe(ctx context.Context, addr string, tlsConf *tls.Config) error {
	listener, err := quic.ListenAddr(addr, tlsConf, &quic.Config{
		// MaxIdleTimeout is QUIC's own transport-level liveness check,
		// beneath the application-level ping/pong hub.Client already does
		// over the stream — belt and suspenders: this one catches a dead
		// path even if something starves the stream-level pump goroutines.
		MaxIdleTimeout:  s.pongWait,
		KeepAlivePeriod: s.pingInterval,
	})
	if err != nil {
		return err
	}
	return s.serve(ctx, listener)
}

// serve runs the accept loop against an already-bound listener — split out
// from ListenAndServe so tests can build their own quic.Listener (getting
// its real, already-bound address back before dialing) instead of racing a
// close-then-rebind-by-address-string against a client dial, which was a
// genuine source of test flakiness under -race, not a product bug.
func (s *Server) serve(ctx context.Context, listener *quic.Listener) error {
	defer func() { _ = listener.Close() }()

	go func() {
		<-ctx.Done()
		_ = listener.Close() // unblocks the Accept loop below on shutdown
	}()

	for {
		conn, err := listener.Accept(ctx)
		if err != nil {
			if ctx.Err() != nil {
				return nil // normal shutdown, not a real failure
			}
			s.log.Warn("quicserver: accept failed", "err", err)
			continue
		}
		go s.serveConn(ctx, conn)
	}
}

// serveConn handles exactly one stream per connection — arkmultitool's
// Http3Publisher opens one stream immediately after connecting and never
// opens a second; a client opening more than one is outside the contract
// and simply has its extra streams ignored (AcceptStream is only called
// once per connection here).
func (s *Server) serveConn(ctx context.Context, conn *quic.Conn) {
	stream, err := conn.AcceptStream(ctx)
	if err != nil {
		s.log.Warn("quicserver: accept stream failed", "remote", conn.RemoteAddr(), "err", err)
		return
	}

	accountID, groupID, serverIP, ok := s.handshake(stream)
	if !ok {
		_ = stream.Close()
		return
	}

	c := hub.NewClient(newQUICConn(stream, s.maxBytes), s.hub, s.store, s.hashes, s.stream, s.log,
		accountID, groupID, serverIP,
		s.pingInterval, s.pongWait, s.writeWait, s.maxEntities)
	c.Run(ctx)
}

// handshake reads and validates the one handshakeRequest frame a client
// must send before any sighting traffic, applying the exact same checks as
// wsserver.Handler.ServeHTTP (JWT verify, then live group-membership
// check) — fail closed before the stream is ever handed to hub.Client, the
// same invariant the WS path keeps before Upgrade.
func (s *Server) handshake(stream *quic.Stream) (accountID, groupID, serverIP string, ok bool) {
	if err := stream.SetReadDeadline(time.Now().Add(10 * time.Second)); err != nil {
		return "", "", "", false
	}
	raw, err := readFrame(stream, maxHandshakeBytes)
	if err != nil {
		s.log.Warn("quicserver: handshake read failed", "err", err)
		return "", "", "", false
	}

	var req handshakeRequest
	if err := json.Unmarshal(raw, &req); err != nil {
		s.reject(stream, "malformed handshake")
		return "", "", "", false
	}
	if req.Token == "" || req.GroupID == "" || req.ServerIP == "" {
		s.reject(stream, "token, group_id and server_ip are all required")
		return "", "", "", false
	}

	accountID, err = s.resolver.Resolve(stream.Context(), req.Token)
	if err != nil {
		s.log.Warn("quicserver: token resolve failed", "err", err)
		s.reject(stream, "invalid token")
		return "", "", "", false
	}

	member, err := s.members.IsMember(stream.Context(), req.GroupID, accountID)
	if err != nil {
		s.log.Error("quicserver: group membership check failed", "err", err, "account_id", accountID, "group_id", req.GroupID)
		s.reject(stream, "membership check failed")
		return "", "", "", false
	}
	if !member {
		s.log.Warn("quicserver: connect refused: not a group member", "account_id", accountID, "group_id", req.GroupID)
		s.reject(stream, "not a member of this group")
		return "", "", "", false
	}

	if err := writeFrame(stream, mustMarshal(handshakeResponse{OK: true})); err != nil {
		s.log.Warn("quicserver: handshake ack write failed", "err", err)
		return "", "", "", false
	}
	return accountID, req.GroupID, req.ServerIP, true
}

func (s *Server) reject(stream *quic.Stream, reason string) {
	_ = writeFrame(stream, mustMarshal(handshakeResponse{OK: false, Error: reason}))
}

func mustMarshal(v handshakeResponse) []byte {
	raw, err := json.Marshal(v)
	if err != nil {
		// handshakeResponse is two plain strings and a bool — Marshal
		// failing here would mean encoding/json itself is broken.
		panic("quicserver: marshal handshakeResponse: " + err.Error())
	}
	return raw
}
