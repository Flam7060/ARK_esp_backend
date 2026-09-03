package hub

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"ark_relay/internal/dedup"
	"ark_relay/internal/protocol"
	"ark_relay/internal/store"
	"ark_relay/internal/streamproducer"
)

// EntityWriter is the subset of *store.EntityStore a Client depends on for
// the always-on live view — narrowed to what's actually called, so tests
// can substitute a fake without dragging in a real Redis connection.
type EntityWriter interface {
	Upsert(ctx context.Context, w store.EntityWrite) error
}

// HashCache is the subset of *store.EntityStore a Client depends on for the
// dedup gate (internal/dedup) — kept separate from EntityWriter because it
// runs on a different cadence (only on change or keyframe expiry, not every
// tick) and answers a different question ("is this worth a Postgres-bound
// fact") than Upsert's ("what does the live view show right now").
type HashCache interface {
	LastHash(ctx context.Context, groupID, serverIP, key string) (hash string, at time.Time, err error)
	SetLastHash(ctx context.Context, groupID, serverIP, key, hash string, at time.Time) error
}

// streamKeyframe forces a re-publish of an unchanged entity at least this
// often, so a consumer relying on last_seen_at recency (see the DTO-sharing
// plan §5b's snapshot-confidence model) doesn't mistake "nobody changed
// this in a while" for "this fell out of the world" purely because the
// dedup gate is doing its job. Mirrors the same keyframe idea already used
// client-side in arkmultitool's ChangeFilter.
const streamKeyframe = 5 * time.Minute

// Client is one authenticated connection, WebSocket or QUIC (see Conn):
// one goroutine reading, one writing (doc §7.1 — "горутина на соединение"),
// talking to each other only through the buffered send channel so a slow
// writer can never block the reader mid-frame.
type Client struct {
	AccountID string
	GroupID   string
	ServerIP  string

	conn      Conn
	hub       *Hub
	store     EntityWriter
	hashCache HashCache
	stream    streamproducer.Publisher
	log       *slog.Logger

	pingInterval time.Duration
	pongWait     time.Duration
	writeWait    time.Duration
	maxEntities  int

	send chan []byte

	closeOnce sync.Once
}

// NewClient wraps an already-authenticated Conn (WebSocket or QUIC) for
// accountID, scoped to the (groupID, serverIP) room. Call Run to start
// serving; Run blocks until the connection ends.
//
// Max frame size is deliberately not a Client concern: each transport
// adapter enforces its own (websocket.Upgrader.SetReadLimit for WS, a
// length-prefix cap for QUIC — see wsserver/quicserver) at the point where
// it actually knows how to reject an oversized frame before buffering it
// whole, which Client's transport-agnostic Conn interface has no way to
// express uniformly.
func NewClient(conn Conn, h *Hub, es EntityWriter, hc HashCache, sp streamproducer.Publisher, log *slog.Logger,
	accountID, groupID, serverIP string,
	pingInterval, pongWait, writeWait time.Duration, maxEntities int,
) *Client {
	return &Client{
		AccountID:    accountID,
		GroupID:      groupID,
		ServerIP:     serverIP,
		conn:         conn,
		hub:          h,
		store:        es,
		hashCache:    hc,
		stream:       sp,
		log:          log,
		pingInterval: pingInterval,
		pongWait:     pongWait,
		writeWait:    writeWait,
		maxEntities:  maxEntities,
		send:         make(chan []byte, 32),
	}
}

// Run registers the client, then blocks running its read and write pumps
// concurrently until either side ends the connection. It always leaves the
// client unregistered and the socket closed on return — ordered teardown,
// no leaked registration.
func (c *Client) Run(ctx context.Context) {
	c.hub.Register(c)
	defer c.hub.Unregister(c)

	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		c.writePump()
	}()
	go func() {
		defer wg.Done()
		c.readPump(ctx)
	}()
	wg.Wait()
}

// closeSlow force-closes the underlying connection from Hub.Broadcast when
// this client's send buffer is full, or from Hub.RevokeAccount when a
// sharing-group kick/leave/delete lands for this account. Closing here
// causes the read pump's next read to fail, which unwinds Run's teardown
// normally — no separate "kicked" flag needed.
func (c *Client) closeSlow() {
	c.closeOnce.Do(func() {
		// nil guard: only real production callers go through NewClient
		// (always a real Conn, WS or QUIC), but a test building a *Client
		// literal to exercise Hub routing/dedup logic shouldn't need to
		// stand up a real socket just to be closeSlow-safe.
		if c.conn != nil {
			_ = c.conn.Close()
		}
	})
}

func (c *Client) readPump(ctx context.Context) {
	var lastSeq uint64
	var sawSeq bool

	for {
		// Deadline reset before every read, not once at start: liveness is
		// "heard anything at all within pongWait", judged the same way for
		// both transports — a sighting batch counts exactly like a pong,
		// since either one proves the connection is alive. This replaces
		// gorilla's WS-specific control-frame pong handler, which QUIC has
		// no equivalent of.
		if err := c.conn.SetReadDeadline(time.Now().Add(c.pongWait)); err != nil {
			return
		}

		raw, err := c.conn.ReadMessage()
		if err != nil {
			return // closed, timed out, or protocol error — teardown handled by caller
		}

		msg, err := protocol.Decode(raw)
		if err != nil {
			c.log.Warn("bad frame, dropping connection", "account_id", c.AccountID, "err", err)
			return // fail closed: a client sending garbage doesn't get a retry loop
		}

		switch msg.Type {
		case protocol.MsgPing:
			c.replyPong()
		case protocol.MsgPong:
			// The read above already proved liveness and reset the
			// deadline; an app-level pong needs no further action.
		case protocol.MsgSighting:
			fillComputedKeys(msg.Entities)
			if err := msg.Validate(c.maxEntities); err != nil {
				c.log.Warn("invalid sighting, dropping connection", "account_id", c.AccountID, "err", err)
				return
			}
			// Doc §7.3: seq confirms the whole batch isn't older than the
			// last accepted one — stale, out-of-order batches are dropped,
			// not merged, since at 1-2s cadence the newer batch already
			// supersedes it entirely.
			if sawSeq && msg.Seq <= lastSeq {
				continue
			}
			sawSeq, lastSeq = true, msg.Seq
			c.handleSighting(ctx, msg)
		}
	}
}

// fillComputedKeys assigns Entity.Key for categories that have no
// client-supplied identity convention (structure/turret — see
// protocol.Entity.Key's doc comment and dedup.Key) before Validate runs.
// Player/dino entities already arrive with a client-computed Key and are
// left untouched.
func fillComputedKeys(entities []protocol.Entity) {
	for i := range entities {
		e := &entities[i]
		if e.Key == "" && (e.Cat == protocol.CategoryStructure || e.Cat == protocol.CategoryTurret) {
			e.Key = dedup.Key(e.Cat, e.ClassName, e.X, e.Y, e.Z)
		}
	}
}

func (c *Client) handleSighting(ctx context.Context, msg protocol.Inbound) {
	now := time.Now().UTC()
	for _, e := range msg.Entities {
		w := store.EntityWrite{
			GroupID: c.GroupID, ServerIP: c.ServerIP, Key: e.Key, Cat: string(e.Cat), Team: e.Team,
			Label: e.Label, X: e.X, Y: e.Y, Z: e.Z,
			ReportedBy: c.AccountID, UpdatedAt: now,
			Tribe: e.Tribe, Status: string(e.Status),
			Health: e.Health, MaxHealth: e.MaxHealth, HeldItem: e.HeldItem,
		}
		if err := c.store.Upsert(ctx, w); err != nil {
			// One entity's write failing (Redis hiccup) doesn't invalidate
			// the rest of the batch or drop the connection — the next
			// sighting a second or two later self-heals it.
			c.log.Error("entity upsert failed", "account_id", c.AccountID, "group_id", c.GroupID, "key", e.Key, "err", err)
		}
		c.maybeStream(ctx, e, now, msg.ReporterCharacterID)
	}

	for _, key := range msg.Vanished {
		c.streamVanished(ctx, key, now)
	}

	out := protocol.Outbound{
		Type: protocol.MsgSighting, ReportedBy: c.AccountID,
		ReporterCharacterID: msg.ReporterCharacterID,
		ReporterX:           msg.ReporterX, ReporterY: msg.ReporterY, ReporterZ: msg.ReporterZ,
		RelayedAt: now.Format(time.RFC3339Nano), Entities: msg.Entities,
	}
	payload, err := protocol.Encode(out)
	if err != nil {
		c.log.Error("encode outbound failed", "err", err)
		return
	}
	c.hub.Broadcast(c.GroupID, c.ServerIP, payload, c)
}

// maybeStream is the dedup gate (DTO-sharing plan §2): fires only when an
// entity's content hash changed since the last Publish, or the keyframe
// elapsed — never on every tick, since that's exactly the Redis-Stream-
// flooding the gate exists to avoid. Players go through the same gate as
// everything else (streamproducer.StreamNameFor routes CategoryPlayer to
// ark:stream:player_sighting), just with an extra identity guard below
// and a different XADD field shape (PlayerFields, not EntityFields).
func (c *Client) maybeStream(ctx context.Context, e protocol.Entity, now time.Time, reporterCharacterID string) {
	stream := streamproducer.StreamNameFor(e.Cat)
	if stream == "" {
		return
	}
	// A player entity with no StableID has no identity for Postgres's
	// player.platform_id (NOT NULL, non-blank) to key on -- publishing it
	// would either fail PlayerSighting's blank-check downstream or, worse,
	// succeed with platform_id="0" and silently merge every unidentified
	// sighting into one bogus shared row. Drop it here, same "not enough
	// data to record" call as get_or_create_tribe makes for a blank
	// tribe_name.
	if e.Cat == protocol.CategoryPlayer && e.StableID == 0 {
		return
	}

	newHash := dedup.ContentHash(e.Cat, e.ClassName, e.X, e.Y, e.Z)
	prevHash, prevAt, err := c.hashCache.LastHash(ctx, c.GroupID, c.ServerIP, e.Key)
	if err != nil {
		c.log.Warn("last hash lookup failed", "key", e.Key, "err", err)
		// Fail toward sending: an unreadable gate must not silently
		// suppress a fact from ever reaching Postgres.
	}

	changed := newHash != prevHash
	expired := !prevAt.IsZero() && now.Sub(prevAt) > streamKeyframe
	if !changed && !expired && err == nil {
		return
	}

	var fields map[string]string
	if e.Cat == protocol.CategoryPlayer {
		fields = streamproducer.PlayerFields(e, c.ServerIP, now, c.AccountID)
	} else {
		fields = streamproducer.EntityFields(e, newHash, c.ServerIP, now, c.AccountID, reporterCharacterID)
	}
	if pubErr := c.stream.Publish(ctx, stream, fields); pubErr != nil {
		c.log.Warn("stream publish failed", "key", e.Key, "stream", stream, "err", pubErr)
		return // don't record a hash for a publish that never happened
	}
	if err := c.hashCache.SetLastHash(ctx, c.GroupID, c.ServerIP, e.Key, newHash, now); err != nil {
		c.log.Warn("set last hash failed", "key", e.Key, "err", err)
	}
}

// streamVanished publishes an explicit "gone" record for a key the client
// used to see and no longer does (DTO-sharing plan §5b) — routed by the
// category embedded in the key's own prefix (dedup.CategoryFromKey), since
// Vanished carries bare keys with no separate category field.
//
// Only structure/turret keys are forwarded: their Key IS "{cat}:{hash}"
// (dedup.Key, computed by fillComputedKeys), so HashFromKey recovers the
// real object_hash. Dino keeps the older, client-supplied
// "{cat}:{label}:{team}" shape — stable identity for the live view as a
// dino walks (a position-grid key would churn every 300 units), but that
// means HashFromKey on a dino key would return "{label}:{team}", not a
// real content hash. Dino identity for Postgres is fuzzy by design anyway
// (proximity-matched, no unique key — models.tamed_dino.object_hash is
// documented as "a signal, not unique") — sending a made-up hash would be
// worse than not sending anything, so dino vanished-signals are dropped
// here, not forwarded with a wrong value.
func (c *Client) streamVanished(ctx context.Context, key string, now time.Time) {
	cat, ok := dedup.CategoryFromKey(key)
	if !ok {
		c.log.Warn("vanished key has no recognizable category prefix", "key", key)
		return
	}
	if cat != protocol.CategoryStructure && cat != protocol.CategoryTurret {
		c.log.Info("vanished signal not forwarded: no recoverable content hash for this category",
			"key", key, "cat", cat)
		return
	}
	stream := streamproducer.StreamNameFor(cat)
	if stream == "" {
		return
	}
	hash, ok := dedup.HashFromKey(key)
	if !ok {
		c.log.Warn("vanished key has no recoverable content hash", "key", key)
		return
	}
	fields := streamproducer.RemovedFields(hash, c.ServerIP, now, c.AccountID)
	if err := c.stream.Publish(ctx, stream, fields); err != nil {
		c.log.Warn("vanished publish failed", "key", key, "stream", stream, "err", err)
	}
}

// pingFrame is precomputed once — every keepalive tick sends the exact same
// bytes, no need to re-encode JSON every pingInterval.
var pingFrame = mustEncode(protocol.Outbound{Type: protocol.MsgPing})

func mustEncode(out protocol.Outbound) []byte {
	raw, err := protocol.Encode(out)
	if err != nil {
		panic("hub: protocol.Encode of a static ping frame failed: " + err.Error())
	}
	return raw
}

// replyPong answers a client-initiated MsgPing — symmetric with the
// server's own keepalive below, though in practice only the server side
// pings today (doc §7.3: relay drives keepalive, clients just answer).
func (c *Client) replyPong() {
	raw, err := protocol.Encode(protocol.Outbound{Type: protocol.MsgPong})
	if err != nil {
		c.log.Error("encode pong failed", "err", err)
		return
	}
	select {
	case c.send <- raw:
	default:
		// Send buffer full — same "too slow, will be dropped by Broadcast
		// or its own read timeout soon anyway" situation as any other
		// backpressure case; a missed pong reply isn't itself fatal.
	}
}

func (c *Client) writePump() {
	ticker := time.NewTicker(c.pingInterval)
	defer ticker.Stop()
	defer func() { _ = c.conn.Close() }()

	for {
		select {
		case payload, ok := <-c.send:
			if !ok {
				return
			}
			_ = c.conn.SetWriteDeadline(time.Now().Add(c.writeWait))
			if err := c.conn.WriteMessage(payload); err != nil {
				return
			}
		case <-ticker.C:
			_ = c.conn.SetWriteDeadline(time.Now().Add(c.writeWait))
			if err := c.conn.WriteMessage(pingFrame); err != nil {
				return
			}
		}
	}
}
