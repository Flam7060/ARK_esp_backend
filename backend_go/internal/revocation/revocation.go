// Package revocation watches for sharing-group membership changes
// backend_python publishes and force-disconnects any live connection that
// lost access — the immediate-effect half of the DTO-sharing plan's group
// membership design (§5): a kicked/removed member shouldn't keep receiving
// (or contributing to) live sightings just because their JWT hasn't
// expired yet.
//
// Deliberately a separate package from internal/hub rather than a method
// that takes a *redis.Client directly: hub's own package doc says it holds
// no durable state and talks to nothing but its in-memory registry — this
// package is the glue between that registry and Redis Pub/Sub, not a new
// responsibility bolted onto Hub itself.
package revocation

import (
	"context"
	"log/slog"
	"strings"

	"github.com/redis/go-redis/v9"

	"ark_relay/internal/hub"
)

// channelPattern subscribes to every group's revocation channel at once —
// one long-lived PSUBSCRIBE, not one SUBSCRIBE per active group, which
// would mean tearing down and re-establishing a subscription every time a
// group's membership goes from zero live connections to one.
const channelPattern = "ark:group:*:revoked"

// Revoker is the subset of *hub.Hub this package depends on.
type Revoker interface {
	RevokeAccount(groupID, accountID string) int
}

var _ Revoker = (*hub.Hub)(nil)

// Watch subscribes to channelPattern and calls h.RevokeAccount for every
// message received, until ctx is done. Intended to run in its own
// goroutine for the lifetime of the process (see cmd/relay/main.go) —
// blocks until ctx cancellation or an unrecoverable subscription error.
func Watch(ctx context.Context, rdb *redis.Client, h Revoker, log *slog.Logger) {
	pubsub := rdb.PSubscribe(ctx, channelPattern)
	defer func() { _ = pubsub.Close() }()

	ch := pubsub.Channel()
	for {
		select {
		case <-ctx.Done():
			return
		case msg, ok := <-ch:
			if !ok {
				return
			}
			groupID, ok := groupIDFromChannel(msg.Channel)
			if !ok {
				log.Warn("revocation: unrecognized channel", "channel", msg.Channel)
				continue
			}
			accountID := msg.Payload
			if accountID == "" {
				log.Warn("revocation: empty account_id payload", "channel", msg.Channel)
				continue
			}
			closed := h.RevokeAccount(groupID, accountID)
			log.Info("revocation processed", "group_id", groupID, "account_id", accountID, "connections_closed", closed)
		}
	}
}

// groupIDFromChannel extracts {group_id} from "ark:group:{group_id}:revoked".
func groupIDFromChannel(channel string) (string, bool) {
	const prefix = "ark:group:"
	const suffix = ":revoked"
	if !strings.HasPrefix(channel, prefix) || !strings.HasSuffix(channel, suffix) {
		return "", false
	}
	groupID := channel[len(prefix) : len(channel)-len(suffix)]
	if groupID == "" {
		return "", false
	}
	return groupID, true
}
