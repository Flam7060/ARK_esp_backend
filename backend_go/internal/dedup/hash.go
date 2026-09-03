// Package dedup computes a content-addressed identity for entities that
// have no stable id of their own in game memory (structures, turrets,
// tamed dinos) -- the hash exists to avoid pushing a Redis Stream fact for
// something already known and unchanged, not for security (see ContentHash
// doc). Used by internal/hub to gate internal/streamproducer writes; the
// live broadcast and Redis HASH+TTL view (internal/store) never consult
// this package -- they show "what's here right now" regardless of whether
// it's new information.
package dedup

import (
	"encoding/hex"
	"fmt"
	"hash/fnv"
	"math"
	"strings"

	"ark_relay/internal/protocol"
)

// gridStep mirrors backend_python's structure_store.DEFAULT_DEDUP_RADIUS.
// The two services can't share a Go constant across the language boundary,
// so this comment is the enforcement: change one, change the other, or a
// structure hashed by the relay and one hashed by the legacy HTTP pipeline
// stop agreeing on identity.
const gridStep = 300.0

func grid(v float64) int64 {
	return int64(math.Round(v / gridStep))
}

// ContentHash hashes the immutable fields of a structure/turret/dino
// sighting. Deliberately excludes:
//   - team/tribe: ownership changing (raided, abandoned) is a new fact
//     worth its own Postgres row, not "the same thing again".
//   - turret dynamic state (ammo/powered/active/targeting): that ticks
//     constantly and is the live HASH+TTL's job, not Postgres history --
//     see kopt::share::TurretInfo on the client side for the same split.
//
// Not a cryptographic hash on purpose: FNV-1a exists here to cut Redis
// Stream traffic, not to resist a deliberate collision attempt -- see the
// package doc comment for the actual reason this exists.
func ContentHash(cat protocol.Category, class string, x, y, z float64) string {
	h := fnv.New64a()
	fmt.Fprintf(h, "%s|%s|%d|%d|%d", cat, class, grid(x), grid(y), grid(z))
	return hex.EncodeToString(h.Sum(nil))
}

// Key builds the wire/Redis identity for a content-addressed entity --
// "{cat}:{contentHash}". Player and dino keep their existing, client-
// supplied "{cat}:{label-or-stable_id}:{team}" shape (see
// protocol.Entity.Key's doc comment) -- this is only for categories that
// never had a Key convention before this plan (structure, turret), and is
// computed server-side (internal/hub's readPump, before Validate) so the
// identity scheme stays consistent regardless of what any given client
// version does or doesn't compute on its own.
func Key(cat protocol.Category, class string, x, y, z float64) string {
	return fmt.Sprintf("%s:%s", cat, ContentHash(cat, class, x, y, z))
}

// HashFromKey recovers the content hash from a Key built by Key(). Used to
// fill "object_hash" on an explicit vanished-signal stream record, where
// the caller has only the bare key string (protocol.Inbound.Vanished),
// not the original class/x/y/z that produced it.
func HashFromKey(key string) (string, bool) {
	idx := strings.IndexByte(key, ':')
	if idx < 0 || idx == len(key)-1 {
		return "", false
	}
	return key[idx+1:], true
}

// CategoryFromKey recovers the category prefix shared by every Key shape
// in this protocol -- player/dino's client-supplied
// "{cat}:{label-or-id}:{team}" and this package's own "{cat}:{hash}" both
// start with "{cat}:". Used to route a bare vanished-key (which carries no
// separate category field) to the right stream.
func CategoryFromKey(key string) (protocol.Category, bool) {
	idx := strings.IndexByte(key, ':')
	if idx <= 0 {
		return "", false
	}
	return protocol.Category(key[:idx]), true
}
