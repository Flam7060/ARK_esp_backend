// Package streamproducer XADDs deduped sighting facts onto per-category
// Redis Streams for backend_python's consumer-group ingestion
// (telemetry-api-v1.md §7/§8, core/redis_stream.py's XREADGROUP pattern
// already proven on ark:stream:player_sighting). Entirely separate from
// the always-on live HASH+broadcast path in internal/store: that answers
// "what's here right now" for connected clients, this feeds the durable
// Postgres copy and only fires when internal/dedup decides something
// actually changed.
package streamproducer

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"

	"ark_relay/internal/protocol"
)

// maxLen bounds each stream so a consumer that's down or falling behind
// doesn't let Redis memory grow without limit -- approximate trimming
// (~MAXLEN) costs O(1) instead of the exact form's O(N), acceptable since
// the bound only needs to be "large enough to survive an outage", not
// exact.
const maxLen = 100_000

// Publisher is the subset of *Producer internal/hub depends on -- narrowed
// so tests can substitute a fake without a real Redis connection, the same
// pattern hub.EntityWriter already uses for internal/store.
type Publisher interface {
	Publish(ctx context.Context, stream string, fields map[string]string) error
}

// Producer XADDs onto whatever stream the caller names.
type Producer struct {
	rdb *redis.Client
}

// New returns a Producer bound to rdb.
func New(rdb *redis.Client) *Producer {
	return &Producer{rdb: rdb}
}

// Publish appends fields as one entry on stream, trimmed to maxLen.
func (p *Producer) Publish(ctx context.Context, stream string, fields map[string]string) error {
	err := p.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: stream,
		MaxLen: maxLen,
		Approx: true,
		Values: fields,
	}).Err()
	if err != nil {
		return fmt.Errorf("streamproducer: xadd %s: %w", stream, err)
	}
	return nil
}

// StreamNameFor picks the durable stream a Category feeds, or "" for
// categories that deliberately don't get one.
//
// CategoryPlayer used to return "" here on the theory that
// ark:stream:player_sighting already had its own, separately-specified
// producer (backend_python/src/services/player_ingest_service.py existed
// before this relay did) -- that producer never materialized anywhere in
// the codebase, so player sightings were silently dropped end to end.
// hub.Client.maybeStream is this relay's own dedup gate for every other
// category; players go through the exact same gate now, just routed to
// PlayerFields instead of EntityFields for the XADD shape.
func StreamNameFor(cat protocol.Category) string {
	switch cat {
	case protocol.CategoryStructure, protocol.CategoryTurret:
		return "ark:stream:structure_sighting"
	case protocol.CategoryDino:
		return "ark:stream:dino_sighting"
	case protocol.CategoryPlayer:
		return "ark:stream:player_sighting"
	default:
		return ""
	}
}

// EntityFields builds the XADD field map for one entity, per the DTO-
// sharing plan's target Python contract (StructureSighting/
// TamedDinoSighting pydantic models) -- StreamFields on that side is
// dict[str, str], so every value here is a string, numbers included.
func EntityFields(e protocol.Entity, objectHash, serverIP string, observedAt time.Time,
	reportedByAccountID, reportedByCharacterID string,
) map[string]string {
	fields := map[string]string{
		"server_ip":                serverIP,
		"class":                    e.ClassName,
		"object_hash":              objectHash,
		"tribe_name":               e.Tribe,
		"team":                     strconv.FormatInt(int64(e.Team), 10),
		"x":                        strconv.FormatFloat(e.X, 'f', -1, 64),
		"y":                        strconv.FormatFloat(e.Y, 'f', -1, 64),
		"z":                        strconv.FormatFloat(e.Z, 'f', -1, 64),
		"observed_at":              observedAt.UTC().Format(time.RFC3339Nano),
		"reported_by_account_id":   reportedByAccountID,
		"reported_by_character_id": reportedByCharacterID,
	}
	// health/max_health omitted rather than sent as a misleading 0 when
	// the client's class doesn't track them (e.g. an indestructible
	// world object) -- distinguishing "zero" from "unknown" matters here
	// exactly like it does in TurretInfo.Ammo/Range on the wire type.
	if e.MaxHealth > 0 {
		fields["health"] = strconv.FormatFloat(e.Health, 'f', -1, 64)
		fields["max_health"] = strconv.FormatFloat(e.MaxHealth, 'f', -1, 64)
	}
	if e.Turret != nil {
		if e.Turret.Ammo != nil {
			fields["turret_ammo"] = strconv.FormatInt(int64(*e.Turret.Ammo), 10)
		}
		if e.Turret.Range != nil {
			fields["turret_range"] = strconv.FormatUint(uint64(*e.Turret.Range), 10)
		}
		fields["turret_powered"] = strconv.FormatBool(e.Turret.Powered)
		fields["turret_active"] = strconv.FormatBool(e.Turret.Active)
	}
	return fields
}

// PlayerFields builds the XADD field map for one player entity, per
// backend_python/src/services/player_ingest_service.py's PlayerSighting
// contract -- a different shape from EntityFields (no object_hash/class/
// turret_*, structures/dinos have no equivalent of platform_id at all).
//
// platform_id is e.StableID (the client's linked_player_data_id),
// stringified -- NOT a real Steam/Epic platform id. The client has no way
// today to read another player's actual platform identity out of ARK's
// process memory (confirmed: no PlayerState/UniqueNetId offset exists
// anywhere in arkmultitool's runtime.cpp/runtime.hpp); linked_player_data_id
// is the only cross-session-stable id available for an observed player,
// same value already used for kopt::share::Sighting::stable_id and for
// dedup Key construction elsewhere. Getting a real platform_id is a
// separate, empirical reverse-engineering task (finding the right offset
// against a live game process), not something this function can solve by
// itself -- until then, every player row this producer creates is
// identified by their in-game character id, not their platform account.
func PlayerFields(e protocol.Entity, serverIP string, observedAt time.Time, reportedByAccountID string) map[string]string {
	fields := map[string]string{
		"server_ip":              serverIP,
		"platform_id":            strconv.FormatUint(e.StableID, 10),
		"x":                      strconv.FormatFloat(e.X, 'f', -1, 64),
		"y":                      strconv.FormatFloat(e.Y, 'f', -1, 64),
		"z":                      strconv.FormatFloat(e.Z, 'f', -1, 64),
		"observed_at":            observedAt.UTC().Format(time.RFC3339Nano),
		"reported_by_account_id": reportedByAccountID,
	}
	if e.Label != "" {
		fields["character_name"] = e.Label
	}
	return fields
}

// RemovedFields builds the XADD fields for an explicit "vanished" signal
// (protocol.Inbound.Vanished) -- object_hash is recovered from the bare
// key itself (see dedup.HashFromKey) since a vanished entity carries no
// fresh class/x/y/z to rehash.
func RemovedFields(objectHash, serverIP string, observedAt time.Time, reportedByAccountID string) map[string]string {
	return map[string]string{
		"object_hash":            objectHash,
		"server_ip":              serverIP,
		"removed":                "true",
		"observed_at":            observedAt.UTC().Format(time.RFC3339Nano),
		"reported_by_account_id": reportedByAccountID,
	}
}
