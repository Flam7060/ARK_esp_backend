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
	// Только для дино: у структур владение выражается трайбом, "приручено"
	// для них не значит ничего. hub.maybeStream уже отсеял диких раньше, но
	// поле всё равно едет -- backend_python's DinoSighting.tamed это вторая
	// линия на случай сообщения от старой сборки релея, и без явного поля
	// она отбраковала бы вообще всех, включая ручных.
	if e.Cat == protocol.CategoryDino {
		fields["tamed"] = strconv.FormatBool(e.Tamed)
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
// platform_id prefers e.SteamID (the real SteamID64, stringified) when the
// client resolved one -- arkmultitool's read_player_steam_id (runtime.cpp)
// found and verified the offset chain live in 2026-09; before that, no
// PlayerState/UniqueNetId offset existed anywhere in the client, and this
// function fell back to e.StableID (linked_player_data_id) unconditionally.
// That fallback stays as-is when SteamID is 0 -- the client legitimately
// can't always resolve it (target's owner disconnected between capture and
// send, e.g.), and a row identified by in-game character id beats no row
// at all.
func PlayerFields(e protocol.Entity, serverIP string, observedAt time.Time, reportedByAccountID string) map[string]string {
	platformID := e.StableID
	if e.SteamID != 0 {
		platformID = e.SteamID
	}
	fields := map[string]string{
		"server_ip":              serverIP,
		"platform_id":            strconv.FormatUint(platformID, 10),
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
