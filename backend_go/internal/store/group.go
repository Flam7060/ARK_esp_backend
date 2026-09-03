package store

import (
	"context"
	"errors"
	"fmt"

	"github.com/redis/go-redis/v9"
)

// GroupMembership answers "is this account currently a member of this
// sharing group" against a Redis SET backend_python's sharing_service.py
// keeps in sync with Postgres group_member on every join/leave/kick (doc:
// DTO-sharing plan §5) -- checked here, not against Postgres directly, so
// a connect attempt doesn't cost a round trip through a service the
// relay otherwise never talks to.
type GroupMembership struct {
	rdb *redis.Client
}

// NewGroupMembership returns a GroupMembership bound to rdb.
func NewGroupMembership(rdb *redis.Client) *GroupMembership {
	return &GroupMembership{rdb: rdb}
}

// IsMember reports whether accountID is currently a member of groupID.
// false with a nil error is the normal "not a member" answer -- callers
// should refuse the connection, not treat it as a fault.
func (g *GroupMembership) IsMember(ctx context.Context, groupID, accountID string) (bool, error) {
	ok, err := g.rdb.SIsMember(ctx, groupMembersKey(groupID), accountID).Result()
	if err != nil {
		return false, fmt.Errorf("store: is member %s/%s: %w", groupID, accountID, err)
	}
	return ok, nil
}

func groupMembersKey(groupID string) string {
	return fmt.Sprintf("ark:group:%s:members", groupID)
}

// ErrNoActiveGroup means the account has no active sharing group set --
// either it never joined/created one, or it left the one it had (see
// core/group_cache.py's clear_active_group). Callers should refuse the
// connection, not treat it as a fault.
var ErrNoActiveGroup = errors.New("store: account has no active group")

// ActiveGroup resolves "which group does this account's sharing route
// into" purely from account_id -- the client no longer states, and the
// relay no longer trusts, a client-declared group_id at all (see
// quicserver.Server.handshake, which calls this instead of reading a
// group_id off the wire). Backed by
// core/group_cache.py's RedisGroupCache.set_active_group/
// clear_active_group, mirroring Postgres account.active_group_id.
func (g *GroupMembership) ActiveGroup(ctx context.Context, accountID string) (string, error) {
	groupID, err := g.rdb.Get(ctx, activeGroupKey(accountID)).Result()
	if errors.Is(err, redis.Nil) {
		return "", ErrNoActiveGroup
	}
	if err != nil {
		return "", fmt.Errorf("store: active group %s: %w", accountID, err)
	}
	return groupID, nil
}

func activeGroupKey(accountID string) string {
	return fmt.Sprintf("ark:account:%s:active_group", accountID)
}
