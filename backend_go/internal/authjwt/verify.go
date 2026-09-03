// Package authjwt verifies the JWT ark_backend issued at login, per
// telemetry-api-v1.md §7.3: the relay checks the signature itself, offline,
// using only the issuer's public key — no round trip to ark_backend on
// every connection.
//
// Token issuance (AUTH) is out of scope here by design (doc §1, A1: "Сама
// выдача JWT — предмет отдельного документа по AUTH"). This package only
// verifies the shape both services already agree on: RS256, with a single
// account_id claim.
//
// account_id is deliberately the ONLY claim the relay trusts from the
// token. Group membership and which game server/character a connection
// belongs to are not baked into the JWT — a token minted once at login
// would go stale the moment sharing-group membership changes (join/leave/
// kick), and re-issuing a token on every membership edit is exactly the
// coupling the group<->relay boundary is meant to avoid. Instead: the
// active group is resolved server-side from account_id alone (never a
// client-declared group_id) and server_ip travels in the connection's
// handshake frame (see quicserver.handshakeRequest), checked against a live Redis
// membership set on every connect (see internal/hub), and per-sighting
// game data (team/tribe/reporter character)
// travels in the message body itself (internal/protocol.Entity/Inbound) —
// the client is the source of truth for its own in-game state, the JWT is
// only proof of which account is speaking.
package authjwt

import (
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// Claims is the subset of the JWT payload the relay relies on — identity
// only. Fan-out scope (active group + server_ip) is not a claim; see the
// package doc comment above for why.
type Claims struct {
	AccountID string `json:"account_id"`
	jwt.RegisteredClaims
}

// Verifier holds the RSA public key used to check token signatures.
type Verifier struct {
	pub *rsa.PublicKey
}

// LoadVerifier reads an RSA public key (PEM, PKIX SubjectPublicKeyInfo)
// from path and returns a Verifier bound to it.
func LoadVerifier(path string) (*Verifier, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("authjwt: read public key %s: %w", path, err)
	}
	block, _ := pem.Decode(raw)
	if block == nil {
		return nil, fmt.Errorf("authjwt: %s: no PEM block found", path)
	}
	key, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("authjwt: %s: parse PKIX public key: %w", path, err)
	}
	pub, ok := key.(*rsa.PublicKey)
	if !ok {
		return nil, fmt.Errorf("authjwt: %s: key is %T, want *rsa.PublicKey (RS256)", path, key)
	}
	return &Verifier{pub: pub}, nil
}

// Verify checks signature, expiry and required claims on raw, and returns
// the decoded Claims. Any failure — bad signature, expired token, missing
// account_id — is reported as a single error; the caller's only correct
// response is to refuse the connection (fail closed).
func (v *Verifier) Verify(raw string) (Claims, error) {
	var claims Claims
	token, err := jwt.ParseWithClaims(raw, &claims, func(t *jwt.Token) (interface{}, error) {
		if _, ok := t.Method.(*jwt.SigningMethodRSA); !ok {
			return nil, fmt.Errorf("unexpected signing method %v", t.Header["alg"])
		}
		return v.pub, nil
	}, jwt.WithValidMethods([]string{"RS256"}), jwt.WithLeeway(5*time.Second))
	if err != nil {
		return Claims{}, fmt.Errorf("authjwt: verify: %w", err)
	}
	if !token.Valid {
		return Claims{}, errors.New("authjwt: token not valid")
	}
	if claims.AccountID == "" {
		return Claims{}, errors.New("authjwt: missing account_id claim")
	}
	return claims, nil
}
