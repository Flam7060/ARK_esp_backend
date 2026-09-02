// Package hub keeps the in-memory registry of live WebSocket connections,
// grouped by sharing group AND game server, and fans "sighting" messages
// out to every other connection in the same room. Nothing here is durable
// — doc §7.2: "Не хранит состояние дольше жизни соединения на своей
// стороне"; on restart or a scale-out to more relay instances, the
// registry is simply empty again, which is correct because Redis
// (internal/store), not the hub, is the source of truth for "who's live
// right now" and "who's currently a group member".
package hub

import (
	"log/slog"
	"sync"
)

// roomKey scopes fan-out to one sharing group on one game server. Sharing
// group (backend_python's sharing_group/group_member, not the ARK tribe) is
// the real "who do I trust" boundary — one account can play multiple ARK
// tribes across servers while sharing with the same real people regardless.
// But group alone isn't enough either: two group members on different
// servers/maps have nothing live to share (their sightings are about
// different worlds), so the room is the intersection of both.
type roomKey struct {
	GroupID  string
	ServerIP string
}

// Hub owns the room->connections registry. One Hub per process; safe for
// concurrent use from every connection's goroutines.
type Hub struct {
	log *slog.Logger

	mu      sync.RWMutex
	rooms   map[roomKey]map[*Client]struct{}
	clients int
}

// New returns an empty Hub.
func New(log *slog.Logger) *Hub {
	return &Hub{log: log, rooms: make(map[roomKey]map[*Client]struct{})}
}

// Register adds c to its room. Called once, from the connection's setup
// path, before its read/write pumps start.
func (h *Hub) Register(c *Client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	key := roomKey{GroupID: c.GroupID, ServerIP: c.ServerIP}
	room, ok := h.rooms[key]
	if !ok {
		room = make(map[*Client]struct{})
		h.rooms[key] = room
	}
	room[c] = struct{}{}
	h.clients++
	h.log.Info("client connected",
		"account_id", c.AccountID, "group_id", c.GroupID, "server_ip", c.ServerIP,
		"room_size", len(room), "total", h.clients)
}

// Unregister removes c from its room, pruning the room itself once empty
// so idle rooms don't accumulate empty maps forever.
func (h *Hub) Unregister(c *Client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	key := roomKey{GroupID: c.GroupID, ServerIP: c.ServerIP}
	room, ok := h.rooms[key]
	if !ok {
		return
	}
	if _, present := room[c]; !present {
		return
	}
	delete(room, c)
	h.clients--
	if len(room) == 0 {
		delete(h.rooms, key)
	}
	h.log.Info("client disconnected",
		"account_id", c.AccountID, "group_id", c.GroupID, "server_ip", c.ServerIP, "total", h.clients)
}

// Broadcast delivers payload to every connection in the (groupID, serverIP)
// room except from — the reporting client's own connection never needs its
// own sighting echoed back (doc §7.3: relay -> "остальным клиентам
// группы").
//
// Delivery is best-effort: a slow reader whose send buffer is full is
// dropped from the room rather than allowed to block the broadcast for
// everyone else (doc §7: "доставки нет гарантии, актуальность важнее").
func (h *Hub) Broadcast(groupID, serverIP string, payload []byte, from *Client) {
	h.mu.RLock()
	room := h.rooms[roomKey{GroupID: groupID, ServerIP: serverIP}]
	targets := make([]*Client, 0, len(room))
	for c := range room {
		if c != from {
			targets = append(targets, c)
		}
	}
	h.mu.RUnlock()

	for _, c := range targets {
		select {
		case c.send <- payload:
		default:
			h.log.Warn("dropping slow subscriber", "account_id", c.AccountID, "group_id", groupID, "server_ip", serverIP)
			c.closeSlow()
		}
	}
}

// RevokeAccount force-closes every live connection for accountID in
// groupID, across every server room that group currently has connections
// in — a kicked/removed member loses access everywhere at once, not just
// on whichever server they happened to be connected to when the group
// membership changed. Returns how many connections were closed, for the
// caller's diagnostics.
func (h *Hub) RevokeAccount(groupID, accountID string) int {
	h.mu.RLock()
	var targets []*Client
	for key, room := range h.rooms {
		if key.GroupID != groupID {
			continue
		}
		for c := range room {
			if c.AccountID == accountID {
				targets = append(targets, c)
			}
		}
	}
	h.mu.RUnlock()

	for _, c := range targets {
		c.closeSlow()
	}
	if len(targets) > 0 {
		h.log.Info("revoked account", "group_id", groupID, "account_id", accountID, "connections_closed", len(targets))
	}
	return len(targets)
}
