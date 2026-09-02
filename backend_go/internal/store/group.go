package store

import (
	"context"
	"fmt"

	"github.com/redis/go-redis/v9"
)

// GroupMembership answers "is this account currently a member of this
// sharing group" against a Redis SET backend_python's sharing_service.py
// keeps in sync with Postgres group_member on every join/leave/kick (doc:
// DTO-sharing plan §5) -- checked here, not against Postgres directly, so
// a WS connect attempt doesn't cost a round trip through a service the
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
