// Package store writes sighted entities into Redis, matching the key
// layout telemetry-api-v1.md §8.1 defines so ark_backend can read the same
// "live" view without a second schema. The relay never touches Postgres —
// doc §7.2: "Не пишет ничего в Postgres напрямую."
package store

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

// entityUpsert is a single Lua script doing HSET + PEXPIRE + ZADD as one
// atomic step (doc §8.1: "Обновляется той же командой... одна
// Lua-транзакция или MULTI, чтобы не разъезжались"). Lua over MULTI because
// it also needs the "now" timestamp computed once and shared between the
// hash field and the zset score without a round trip.
//
// This script only ever touches the fields listed in ARGV below -- it
// deliberately never writes content_hash/streamed_at (see LastHash/
// SetLastHash), so the dedup gate's memory of "last streamed" survives
// across every ordinary Upsert refreshing the live view.
//
// KEYS[1] = entity hash key   ark:group:{group_id}:server:{server_ip}:entity:{key}
// KEYS[2] = room index zset   ark:group:{group_id}:server:{server_ip}:entities
// ARGV[1] = member (the entity Key, also the zset member)
// ARGV[2] = cat
// ARGV[3] = team
// ARGV[4] = label
// ARGV[5] = x
// ARGV[6] = y
// ARGV[7] = z
// ARGV[8] = reported_by (account_id)
// ARGV[9] = updated_at (unix ms)
// ARGV[10] = ttl_seconds
// ARGV[11] = tribe (may be empty)
// ARGV[12] = status (may be empty)
// ARGV[13] = health
// ARGV[14] = max_health
// ARGV[15] = held_item (may be empty)
// ARGV[16] = tamed ("1"/"0") -- only meaningful for cat == dino; read by
//            backend_python's density aggregation to count tames per cell
//            without re-deriving ownership from team heuristics.
const entityUpsertScript = `
redis.call('HSET', KEYS[1],
  'cat', ARGV[2], 'team', ARGV[3], 'label', ARGV[4],
  'x', ARGV[5], 'y', ARGV[6], 'z', ARGV[7],
  'reported_by', ARGV[8], 'updated_at', ARGV[9],
  'tribe', ARGV[11], 'status', ARGV[12],
  'health', ARGV[13], 'max_health', ARGV[14], 'held_item', ARGV[15],
  'tamed', ARGV[16])
redis.call('EXPIRE', KEYS[1], ARGV[10])
redis.call('ZADD', KEYS[2], ARGV[9], ARGV[1])
return 1
`

// EntityWrite is one entity sighting ready to persist, already validated
// and keyed by the caller (internal/hub). Trailing fields are optional --
// left at their zero value when the producer doesn't track them.
type EntityWrite struct {
	GroupID    string
	ServerIP   string
	Key        string // "{cat}:{stable_id-or-label}:{team}", or "{cat}:{contentHash}" for structure/turret (see dedup.Key)
	Cat        string
	Team       int32
	Label      string
	X, Y, Z    float64
	ReportedBy string
	UpdatedAt  time.Time
	Tribe      string
	Status     string
	Health     float64
	MaxHealth  float64
	HeldItem   string
	// Tamed mirrors protocol.Entity.Tamed -- only meaningful for cat ==
	// dino. Kept in the live view (not just the durable stream) because the
	// density aggregation reads this layer, not Postgres.
	Tamed bool
}

// EntityStore writes live entity sightings to Redis under the shared
// ark:group:*:server:* namespace (§8.1, updated for group×server routing).
type EntityStore struct {
	rdb    *redis.Client
	script *redis.Script
	ttl    time.Duration
}

// NewEntityStore returns an EntityStore bound to rdb, expiring entities
// after ttl of no refresh.
func NewEntityStore(rdb *redis.Client, ttl time.Duration) *EntityStore {
	return &EntityStore{rdb: rdb, script: redis.NewScript(entityUpsertScript), ttl: ttl}
}

// Upsert writes w atomically: the entity hash (with TTL) and the room's
// score-sorted index used for "everyone visible right now" reads.
func (s *EntityStore) Upsert(ctx context.Context, w EntityWrite) error {
	hashKey := entityHashKey(w.GroupID, w.ServerIP, w.Key)
	indexKey := roomIndexKey(w.GroupID, w.ServerIP)
	ttlSeconds := int64(s.ttl / time.Second)

	err := s.script.Run(ctx, s.rdb, []string{hashKey, indexKey},
		w.Key, w.Cat, w.Team, w.Label, w.X, w.Y, w.Z,
		w.ReportedBy, w.UpdatedAt.UnixMilli(), ttlSeconds, w.Tribe, w.Status,
		w.Health, w.MaxHealth, w.HeldItem, boolField(w.Tamed),
	).Err()
	if err != nil {
		return fmt.Errorf("store: upsert entity %s/%s/%s: %w", w.GroupID, w.ServerIP, w.Key, err)
	}
	return nil
}

// LastHash returns the content hash and timestamp of the last time
// internal/dedup decided this entity was worth an XADD (see
// internal/streamproducer) -- distinct from Upsert's live-view fields,
// read/written independently since the dedup gate runs on a different
// cadence (only on change or keyframe expiry) than the always-on live
// write. Zero values (empty hash, zero time) mean "never streamed" --
// callers treat that as an unconditional first send, not an error.
func (s *EntityStore) LastHash(ctx context.Context, groupID, serverIP, key string) (hash string, at time.Time, err error) {
	hashKey := entityHashKey(groupID, serverIP, key)
	vals, err := s.rdb.HMGet(ctx, hashKey, "content_hash", "streamed_at").Result()
	if err != nil {
		return "", time.Time{}, fmt.Errorf("store: last hash %s: %w", hashKey, err)
	}
	if s, ok := vals[0].(string); ok {
		hash = s
	}
	if s, ok := vals[1].(string); ok && s != "" {
		if ms, parseErr := strconv.ParseInt(s, 10, 64); parseErr == nil {
			at = time.UnixMilli(ms)
		}
	}
	return hash, at, nil
}

// SetLastHash records that hash was just streamed for key at at --
// internal/hub calls this only after a successful Publish, so a failed
// XADD doesn't get remembered as "already sent" and silently skipped on
// the next tick.
func (s *EntityStore) SetLastHash(ctx context.Context, groupID, serverIP, key, hash string, at time.Time) error {
	hashKey := entityHashKey(groupID, serverIP, key)
	if err := s.rdb.HSet(ctx, hashKey, "content_hash", hash, "streamed_at", at.UnixMilli()).Err(); err != nil {
		return fmt.Errorf("store: set last hash %s: %w", hashKey, err)
	}
	return nil
}

// boolField renders a bool as the "1"/"0" the hash field carries -- Lua has
// no bool argument type over the redis protocol, and "true"/"false" strings
// would push the parsing quirk onto every reader instead of settling it
// here once.
func boolField(v bool) string {
	if v {
		return "1"
	}
	return "0"
}

func entityHashKey(groupID, serverIP, key string) string {
	return fmt.Sprintf("ark:group:%s:server:%s:entity:%s", groupID, serverIP, key)
}

func roomIndexKey(groupID, serverIP string) string {
	return fmt.Sprintf("ark:group:%s:server:%s:entities", groupID, serverIP)
}

// Ping verifies connectivity to Redis at startup, so a misconfigured
// address fails the process immediately instead of on the first sighting.
func Ping(ctx context.Context, rdb *redis.Client) error {
	if err := rdb.Ping(ctx).Err(); err != nil {
		return fmt.Errorf("store: redis ping: %w", err)
	}
	return nil
}
