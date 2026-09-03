// Package protocol defines the JSON wire format exchanged over the
// ark_relay QUIC stream, per telemetry-api-v1.md §7.3. The same shape is
// used symmetrically client->relay and relay->subscribers.
package protocol

import (
	"encoding/json"
	"errors"
	"fmt"
)

// MsgType discriminates the "type" field of every relay message.
type MsgType string

const (
	MsgSighting MsgType = "sighting"
	MsgPing     MsgType = "ping"
	MsgPong     MsgType = "pong"
)

// Category mirrors kopt::share::Kind (arkmultitool/include/kopt/share.hpp)
// — that DTO is the source of truth for this wire shape, per the
// DTO-sharing plan: the client decides what a sighting looks like, the
// relay just carries it. CategoryTurret/CategoryStructure are new; unlike
// player/dino they never went through this relay before this plan —
// structures/turrets used to be a separate HTTP pipeline straight into
// backend_python (see the historical note on Entity.Key below).
type Category string

const (
	CategoryPlayer    Category = "player"
	CategoryDino      Category = "dino"
	CategoryTurret    Category = "turret"
	CategoryStructure Category = "structure"
)

func (c Category) valid() bool {
	switch c {
	case CategoryPlayer, CategoryDino, CategoryTurret, CategoryStructure:
		return true
	default:
		return false
	}
}

// Status is the sighted actor's live ESP state -- distinct from Cat, which
// is what it is; Status is what it's doing right now. Optional: a producer
// that doesn't track this (dinos, older clients) just omits it.
type Status string

const (
	StatusAwake      Status = "awake"
	StatusSleeping   Status = "sleeping"
	StatusKnockedOut Status = "knocked_out"
	StatusDead       Status = "dead"
)

func (s Status) valid() bool {
	switch s {
	case "", StatusAwake, StatusSleeping, StatusKnockedOut, StatusDead:
		return true
	default:
		return false
	}
}

// TurretInfo mirrors kopt::share::TurretInfo. Present only when
// Entity.Cat == CategoryTurret. Ammo/Range are pointers, not bare values,
// so "the client couldn't read this field" (nil) stays distinguishable
// from "read as zero" (Ammo: 0, e.g. an empty turret) — collapsing that
// distinction would make an empty turret indistinguishable from one whose
// ammo count simply isn't known yet.
type TurretInfo struct {
	Ammo           *int32 `json:"ammo,omitempty"`
	Range          *uint8 `json:"range,omitempty"`
	Targeting      *uint8 `json:"targeting,omitempty"`
	Warning        *uint8 `json:"warning,omitempty"`
	Powered        bool   `json:"powered,omitempty"`
	Active         bool   `json:"active,omitempty"`
	TargetingActor bool   `json:"targeting_actor,omitempty"`
}

// Entity is one sighted object inside a "sighting" message. Field set
// matches the "live" subset of §5.3/§5.4 — no history-only fields
// (health/level/tribe_name are deliberately intact here per §7.2: the relay
// shares everything a client currently sees via ESP, not just self).
//
// Tribe/Status/Health/MaxHealth/HeldItem were added for the KOPT client
// integration: the client is the source of truth for what it currently
// sees, and the relay is purely a fan-out/live-view layer (not the durable
// store -- that's backend_python), so extending this wire shape doesn't
// touch the persistence contract at all. All optional so existing
// producers that don't send them keep working unchanged.
//
// Structures/turrets used to be entirely absent from this channel (a
// separate HTTP pipeline fed backend_python's structure_store directly).
// They're in scope now, per the DTO-sharing plan -- Class/Turret exist for
// them; Key for these two categories is computed by the relay itself, not
// sent by the client (see dedup.Key) -- unlike player/dino, which have no
// established stable id in game memory for the relay to derive one from,
// so the client remains the source of truth for their Key.
type Entity struct {
	// SchemaVersion travels on every message rather than being negotiated
	// once, matching kopt::share::kSchemaVersion -- this channel has no
	// session-level handshake to pin a version to. 0 (unset) is treated as
	// version 1 for backward compatibility with producers written before
	// this field existed.
	SchemaVersion uint8 `json:"v,omitempty"`
	// Key is the composite identity from FR-6/§7.3: "{cat}:{label}:{team}"
	// for dinos, or "{cat}:{stable_id}:{team}" for players when the client
	// has one (see kopt::Actor::linked_player_data_id on the C++ side) --
	// display names collide (two players can share a nickname) in a way
	// this in-game account id doesn't. Never the in-memory Addr — the
	// relay has no game process to read one from, and Addr isn't stable
	// across client sessions anyway.
	//
	// For structure/turret, this is instead filled in by the relay itself
	// (dedup.Key: "{cat}:{contentHash}") before validation -- may arrive
	// empty from the client for these two categories.
	Key       string      `json:"key"`
	Cat       Category    `json:"cat"`
	Team      int32       `json:"team"`
	Label     string      `json:"label,omitempty"`
	ClassName string      `json:"class,omitempty"`
	X         float64     `json:"x"`
	Y         float64     `json:"y"`
	Z         float64     `json:"z"`
	Tribe     string      `json:"tribe,omitempty"`
	Status    Status      `json:"status,omitempty"`
	Health    float64     `json:"health,omitempty"`
	MaxHealth float64     `json:"max_health,omitempty"`
	HeldItem  string      `json:"held_item,omitempty"`
	Turret    *TurretInfo `json:"turret,omitempty"`
	// StableID is the client's linked_player_data_id, set only for
	// Cat == CategoryPlayer (kopt::share::Sighting::stable_id is 0 for
	// every other category -- no such id exists in game memory for
	// dino/structure/turret). Not a real platform/Steam id -- see
	// streamproducer.PlayerFields' own doc comment on why this is what's
	// available today and how it's used downstream.
	StableID uint64 `json:"stable_id,omitempty"`
}

// Validate rejects an Entity that would corrupt the Redis live-view if
// written as-is. Called once at the message boundary (Inbound.Validate),
// after the relay has already filled in Key for structure/turret
// (internal/hub's readPump) -- never re-validated downstream.
func (e Entity) Validate() error {
	if e.Key == "" {
		return errors.New("entity: key is required")
	}
	if !e.Cat.valid() {
		return fmt.Errorf("entity: unknown cat %q", e.Cat)
	}
	switch e.Cat {
	case CategoryStructure, CategoryTurret:
		// Structures/turrets have no display label worth requiring --
		// class is their actual identity (feeds both Key and object_hash).
		if e.ClassName == "" {
			return errors.New("entity: class is required for structure/turret")
		}
	default:
		if e.Label == "" {
			return errors.New("entity: label is required")
		}
	}
	if e.Cat == CategoryTurret && e.Turret == nil {
		return errors.New("entity: turret info is required for turret category")
	}
	if !e.Status.valid() {
		return fmt.Errorf("entity: unknown status %q", e.Status)
	}
	return nil
}

// Inbound is a message read from a client connection. Only "sighting" and
// "ping" are legal inbound types; "pong" is inbound too (keepalive reply)
// but carries no payload.
type Inbound struct {
	Type MsgType `json:"type"`
	Seq  uint64  `json:"seq"`
	// ReporterCharacterID is the sender's current in-game character id --
	// data about this session, not identity (that's the JWT's account_id).
	// One account can have characters across multiple servers/tribes, so
	// this can't be inferred from AccountID alone; the client is the only
	// side that knows it.
	ReporterCharacterID string `json:"reporter_character_id,omitempty"`
	// ReporterX/Y/Z is the reporter's own world position at the time of
	// this batch, paired with ReporterCharacterID above -- both optional,
	// both informational only (never used for authorization; account_id
	// from the verified token remains the sole trust boundary). Lets
	// receivers dedup a whole batch from a teammate who's already in view
	// range instead of redrawing the same base twice (see arkmultitool's
	// kopt::share::ReporterFilter, the actual consumer on the client side).
	// Zero (the unset default) reads as "position unknown" -- indistinguishable
	// from a genuine origin-adjacent position, but no real ARK map
	// coordinate lands there, so the only real-world effect of an
	// old/non-participating client omitting these is "never deduped",
	// never silent data loss.
	ReporterX float64 `json:"reporter_x,omitempty"`
	ReporterY float64 `json:"reporter_y,omitempty"`
	ReporterZ float64 `json:"reporter_z,omitempty"`
	// Vanished lists Entity.Key values the client no longer sees that it
	// did see in a previous batch -- an explicit "this is gone" signal
	// (kopt::share::ChangeFilter::collect_vanished on the C++ side),
	// stronger than silence/TTL expiry because it means someone actually
	// rescanned that spot and found nothing, not just stopped looking.
	Vanished []string `json:"vanished,omitempty"`
	Entities []Entity `json:"entities,omitempty"`
}

// Validate checks structural invariants of an inbound "sighting" message
// against the given bound on batch size (config.MaxEntitiesPerMessage).
// maxEntities <= 0 disables the bound (used only in tests).
func (m Inbound) Validate(maxEntities int) error {
	if m.Type != MsgSighting {
		return nil
	}
	if maxEntities > 0 && len(m.Entities) > maxEntities {
		return fmt.Errorf("sighting: %d entities exceeds limit %d", len(m.Entities), maxEntities)
	}
	if maxEntities > 0 && len(m.Vanished) > maxEntities {
		return fmt.Errorf("sighting: %d vanished keys exceeds limit %d", len(m.Vanished), maxEntities)
	}
	for i, e := range m.Entities {
		if err := e.Validate(); err != nil {
			return fmt.Errorf("sighting: entity[%d]: %w", i, err)
		}
	}
	for i, key := range m.Vanished {
		if key == "" {
			return fmt.Errorf("sighting: vanished[%d]: empty key", i)
		}
	}
	return nil
}

// Outbound is a "sighting" fan-out to subscribers of the reporting
// client's group room (same group_id, same server_ip), or a keepalive
// frame.
type Outbound struct {
	Type MsgType `json:"type"`
	// ReportedBy is the verified account_id from the sender's token --
	// the only trust-relevant identity field here, resolved server-side
	// (hub.Client.AccountID), never client-declared.
	ReportedBy string `json:"reported_by,omitempty"`
	// ReporterCharacterID/ReporterX/Y/Z are the Inbound fields of the same
	// name, forwarded through unchanged (see Inbound.ReporterCharacterID's
	// own doc comment) -- client-declared, informational only, never used
	// for authorization or room routing, distinct from ReportedBy above.
	ReporterCharacterID string   `json:"reporter_character_id,omitempty"`
	ReporterX           float64  `json:"reporter_x,omitempty"`
	ReporterY           float64  `json:"reporter_y,omitempty"`
	ReporterZ           float64  `json:"reporter_z,omitempty"`
	RelayedAt           string   `json:"relayed_at,omitempty"`
	Entities            []Entity `json:"entities,omitempty"`
}

// Decode parses one length-prefixed stream frame into an Inbound message.
func Decode(raw []byte) (Inbound, error) {
	var m Inbound
	if err := json.Unmarshal(raw, &m); err != nil {
		return Inbound{}, fmt.Errorf("protocol: decode: %w", err)
	}
	return m, nil
}

// Encode serializes an Outbound message into one stream frame's payload.
func Encode(m Outbound) ([]byte, error) {
	b, err := json.Marshal(m)
	if err != nil {
		return nil, fmt.Errorf("protocol: encode: %w", err)
	}
	return b, nil
}
