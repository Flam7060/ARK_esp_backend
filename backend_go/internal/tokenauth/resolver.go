// Package tokenauth is the single point where wsserver and quicserver
// turn a client-presented bearer credential into an account_id, accepting
// either of the two shapes core/account_auth.py's get_current_account
// already accepts on the HTTP side: an RS256 JWT (verified offline,
// internal/authjwt -- no round trip anywhere) or a self-service api_key
// (resolved via a Redis cache, internal/apikeycache -- one Redis round
// trip, never a call to backend_python). A key exists specifically for
// accounts that never got a JWT injected at launch (see arkmultitool's
// Diagnostics-tab API key field), not as a lesser fallback for accounts
// that did -- JWT is tried first only because it's the common case and
// needs no Redis at all, not because it's "preferred."
package tokenauth

import (
	"context"
	"fmt"

	"ark_relay/internal/authjwt"
)

// KeyResolver is the subset of apikeycache.Cache this package depends on
// -- narrowed so tests can substitute a fake without a real Redis
// connection, same pattern as wsserver.GroupChecker.
type KeyResolver interface {
	Resolve(ctx context.Context, token string) (accountID string, err error)
}

// Resolver composes JWT verification and api_key cache resolution behind
// one Resolve call.
type Resolver struct {
	verifier *authjwt.Verifier
	keys     KeyResolver
}

// New builds a Resolver. keys may be nil -- api_key resolution is then
// simply unavailable (every token that fails JWT verification is
// rejected outright), which is exactly what every existing caller got
// before this package existed; tests that don't care about api_key
// fallback can pass nil instead of standing up a fake Redis.
func New(verifier *authjwt.Verifier, keys KeyResolver) *Resolver {
	return &Resolver{verifier: verifier, keys: keys}
}

// Resolve returns the account_id token proves control of. JWT is tried
// first; only a token that fails JWT verification is tried against the
// api_key cache, and only if one was configured. Both failing is reported
// as one combined error -- the caller's only correct response either way
// is to refuse the connection (fail closed), so there's no behavioral
// difference to preserve by keeping the two errors apart, only strictly
// more useful diagnostics in the log line either error ends up in.
func (r *Resolver) Resolve(ctx context.Context, token string) (string, error) {
	claims, jwtErr := r.verifier.Verify(token)
	if jwtErr == nil {
		return claims.AccountID, nil
	}
	if r.keys == nil {
		return "", fmt.Errorf("tokenauth: not a valid JWT and no api_key cache configured: %w", jwtErr)
	}
	accountID, keyErr := r.keys.Resolve(ctx, token)
	if keyErr == nil {
		return accountID, nil
	}
	// fmt.Errorf wrapping two %w verbs (Go 1.20+) -- errors.Is/As against
	// either the JWT or the api_key failure still works on the result.
	return "", fmt.Errorf("tokenauth: neither a valid JWT (%w) nor a known api_key (%w)", jwtErr, keyErr)
}
