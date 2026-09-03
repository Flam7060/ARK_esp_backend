// Package wsserver exposes the single HTTP endpoint that upgrades to the
// ark_relay WebSocket protocol, after verifying the caller's JWT and their
// live sharing-group membership.
package wsserver

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/gorilla/websocket"

	"ark_relay/internal/hub"
	"ark_relay/internal/store"
	"ark_relay/internal/streamproducer"
	"ark_relay/internal/tokenauth"
)

// GroupChecker is the subset of *store.GroupMembership Handler depends on
// — narrowed so tests can substitute a fake without a real Redis
// connection.
type GroupChecker interface {
	IsMember(ctx context.Context, groupID, accountID string) (bool, error)
	// ActiveGroup resolves the group this account currently shares into,
	// purely from account_id -- the client sends no group_id at all
	// anymore (see ServeHTTP's own comment on why). Returns
	// store.ErrNoActiveGroup if the account has none set.
	ActiveGroup(ctx context.Context, accountID string) (string, error)
}

// Handler upgrades authenticated, authorized requests to WebSocket
// connections and hands them to the Hub.
type Handler struct {
	hub      *hub.Hub
	resolver *tokenauth.Resolver
	store    hub.EntityWriter
	hashes   hub.HashCache
	stream   streamproducer.Publisher
	members  GroupChecker
	log      *slog.Logger
	upgrader websocket.Upgrader

	pingInterval time.Duration
	pongWait     time.Duration
	writeWait    time.Duration
	maxEntities  int
	maxBytes     int64
}

// New builds a Handler. Deadlines and limits come from config, not
// literals, so retuning them is a config edit.
func New(h *hub.Hub, resolver *tokenauth.Resolver, es hub.EntityWriter, hc hub.HashCache, sp streamproducer.Publisher,
	members GroupChecker, log *slog.Logger,
	pingInterval, pongWait, writeWait time.Duration, maxEntities int, maxBytes int64,
) *Handler {
	return &Handler{
		hub: h, resolver: resolver, store: es, hashes: hc, stream: sp, members: members, log: log,
		upgrader: websocket.Upgrader{
			ReadBufferSize:  4096,
			WriteBufferSize: 4096,
			// Auth here is the bearer JWT, not same-origin — the relay is
			// meant to serve both the native Go client and a possible web
			// client from a different origin (doc §7.3). Rejecting on
			// origin would reject the legitimate case; the JWT check below
			// is what actually gates access.
			CheckOrigin: func(*http.Request) bool { return true },
		},
		pingInterval: pingInterval, pongWait: pongWait, writeWait: writeWait,
		maxEntities: maxEntities, maxBytes: maxBytes,
	}
}

// ServeHTTP verifies the request's JWT, resolves the caller's active
// sharing group server-side (never trusts a client-declared group_id —
// there isn't one on the wire to trust), checks live membership in it,
// then upgrades to WebSocket and blocks running the connection until it
// ends. Every failure — auth, no active group, missing params, not a
// member — never reaches the upgrade: fail closed before the socket
// exists.
func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	token := bearerToken(r)
	if token == "" {
		http.Error(w, "missing bearer token", http.StatusUnauthorized)
		return
	}

	accountID, err := h.resolver.Resolve(r.Context(), token)
	if err != nil {
		h.log.Warn("token resolve failed", "err", err, "remote", r.RemoteAddr)
		http.Error(w, "invalid token", http.StatusUnauthorized)
		return
	}

	serverIP := r.URL.Query().Get("server_ip")
	if serverIP == "" {
		http.Error(w, "server_ip query parameter is required", http.StatusBadRequest)
		return
	}

	// group_id comes only from server-side resolution by account_id, never
	// from the client — a caller can't connect "as" a different group just
	// by asking for one (there's no field left to ask with at all).
	groupID, err := h.members.ActiveGroup(r.Context(), accountID)
	if errors.Is(err, store.ErrNoActiveGroup) {
		h.log.Warn("connect refused: no active group", "account_id", accountID)
		http.Error(w, "account has no active sharing group", http.StatusForbidden)
		return
	}
	if err != nil {
		h.log.Error("active group resolve failed", "err", err, "account_id", accountID)
		http.Error(w, "active group resolve failed", http.StatusInternalServerError)
		return
	}

	// Still checked, not just trusted from the active-group cache: a
	// second, independent signal against the same staleness class
	// IsMember already guards everywhere else (see its own doc).
	member, err := h.members.IsMember(r.Context(), groupID, accountID)
	if err != nil {
		h.log.Error("group membership check failed", "err", err, "account_id", accountID, "group_id", groupID)
		http.Error(w, "membership check failed", http.StatusInternalServerError)
		return
	}
	if !member {
		h.log.Warn("connect refused: not a group member", "account_id", accountID, "group_id", groupID)
		http.Error(w, "not a member of this group", http.StatusForbidden)
		return
	}

	conn, err := h.upgrader.Upgrade(w, r, nil)
	if err != nil {
		h.log.Warn("websocket upgrade failed", "err", err, "account_id", accountID)
		return // Upgrade already wrote the HTTP error response itself
	}

	c := hub.NewClient(newWSConn(conn, h.maxBytes), h.hub, h.store, h.hashes, h.stream, h.log,
		accountID, groupID, serverIP,
		h.pingInterval, h.pongWait, h.writeWait, h.maxEntities)
	c.Run(r.Context())
}

// bearerToken reads the JWT from the Authorization header (native Go
// client) or the ?token= query parameter (browser WebSocket API can't set
// arbitrary headers on the handshake — doc §7.3).
func bearerToken(r *http.Request) string {
	if auth := r.Header.Get("Authorization"); auth != "" {
		if rest, ok := strings.CutPrefix(auth, "Bearer "); ok {
			return rest
		}
	}
	return r.URL.Query().Get("token")
}
