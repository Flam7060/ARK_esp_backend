// Command devtoken mints a signed RS256 JWT for local testing of
// ark_relay, standing in for the real AUTH service (telemetry-api-v1.md
// §1 A1: token issuance is a separate, not-yet-written document). It is a
// development tool only — never wire this into a running deployment; the
// private key it takes must never be the one guarding production tokens.
package main

import (
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// claims mirrors authjwt.Claims exactly (json tag included) — devtoken
// signs a *test* token in the same shape the real ark_backend login issues,
// not a superset: group/server-ip scope is dynamic (active group resolved
// server-side from account_id, server_ip checked per-connect against
// ?server_ip=), never baked into the JWT (see authjwt.Claims doc for why).
type claims struct {
	AccountID string `json:"account_id"`
	jwt.RegisteredClaims
}

func main() {
	keyPath := flag.String("key", "", "path to RSA private key PEM (PKCS1 or PKCS8)")
	accountID := flag.String("account-id", "", "account_id claim")
	ttl := flag.Duration("ttl", time.Hour, "token lifetime")
	flag.Parse()

	if *keyPath == "" || *accountID == "" {
		fmt.Fprintln(os.Stderr, "usage: devtoken -key priv.pem -account-id <uuid> [-ttl 1h]")
		os.Exit(2)
	}

	key, err := loadPrivateKey(*keyPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "devtoken:", err)
		os.Exit(1)
	}

	now := time.Now()
	c := claims{
		AccountID: *accountID,
		RegisteredClaims: jwt.RegisteredClaims{
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(now.Add(*ttl)),
		},
	}

	token := jwt.NewWithClaims(jwt.SigningMethodRS256, c)
	signed, err := token.SignedString(key)
	if err != nil {
		fmt.Fprintln(os.Stderr, "devtoken: sign:", err)
		os.Exit(1)
	}
	fmt.Println(signed)
}

func loadPrivateKey(path string) (*rsa.PrivateKey, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	block, _ := pem.Decode(raw)
	if block == nil {
		return nil, fmt.Errorf("%s: no PEM block found", path)
	}
	if key, err := x509.ParsePKCS1PrivateKey(block.Bytes); err == nil {
		return key, nil
	}
	keyAny, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("%s: not a PKCS1 or PKCS8 RSA private key: %w", path, err)
	}
	key, ok := keyAny.(*rsa.PrivateKey)
	if !ok {
		return nil, fmt.Errorf("%s: key is %T, want *rsa.PrivateKey", path, keyAny)
	}
	return key, nil
}
