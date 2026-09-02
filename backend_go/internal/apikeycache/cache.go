// Package apikeycache resolves a self-service api_key (backend_python's
// routers/v1/api_keys.py -- an opaque token, not a JWT) to the account_id
// that owns it, via a Redis cache backend_python writes to on
// create/revoke (see core/api_key_cache.py). This package never talks to
// backend_python directly and never sees the pepper Postgres's own
// api_key.key_hash is hashed with (core/tokens.hash_token): the lookup
// key here is a *separate*, unpeppered SHA-256(plaintext) digest,
// computed identically on both sides -- see core/api_key_cache.py's doc
// comment for why that's fine for this cache specifically (Redis has no
// external port in docker-compose, and the threat this pepper defends
// against -- an offline DB dump -- doesn't apply to an ephemeral,
// TTL-bounded cache entry the same way).
package apikeycache

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"

	"github.com/redis/go-redis/v9"
)

// ErrNotFound means the token isn't a cached api_key -- either it never
// was one (most often: it's a JWT, and the caller should have tried
// authjwt.Verify first -- see internal/tokenauth), or it was but expired
// or got revoked (core/api_key_cache.py's RedisApiKeyCache.delete_key runs
// synchronously in the same request that flips api_key.status_code, so a
// revoked key stops resolving essentially immediately, not just after its
// TTL).
var ErrNotFound = errors.New("apikeycache: key not found or expired")

// getter is the subset of *redis.Client this package needs -- narrowed so
// tests can substitute a fake without a real Redis connection, the same
// pattern wsserver.GroupChecker already uses for group membership.
type getter interface {
	Get(ctx context.Context, key string) *redis.StringCmd
}

// Cache resolves tokens against rdb.
type Cache struct {
	rdb getter
}

// New builds a Cache bound to rdb (typically the same *redis.Client
// cmd/relay/main.go already holds for internal/store).
func New(rdb getter) *Cache {
	return &Cache{rdb: rdb}
}

func lookupKey(token string) string {
	sum := sha256.Sum256([]byte(token))
	return "ark:api_key:tok:" + hex.EncodeToString(sum[:])
}

// Resolve returns the account_id backend_python cached for token, or
// ErrNotFound.
func (c *Cache) Resolve(ctx context.Context, token string) (string, error) {
	accountID, err := c.rdb.Get(ctx, lookupKey(token)).Result()
	if errors.Is(err, redis.Nil) {
		return "", ErrNotFound
	}
	if err != nil {
		return "", fmt.Errorf("apikeycache: redis get: %w", err)
	}
	return accountID, nil
}
