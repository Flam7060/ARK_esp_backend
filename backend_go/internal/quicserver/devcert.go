package quicserver

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"fmt"
	"math/big"
	"time"
)

// LoadTLSConfig reads a real cert/key pair from disk for the QUIC listener
// — the production counterpart to GenerateDevTLSConfig below. NextProtos
// must be set here (not left to the caller) because quic-go refuses a TLS
// config with no ALPN protocols configured at all.
func LoadTLSConfig(certFile, keyFile string) (*tls.Config, error) {
	cert, err := tls.LoadX509KeyPair(certFile, keyFile)
	if err != nil {
		return nil, fmt.Errorf("quicserver: load cert/key: %w", err)
	}
	return &tls.Config{
		Certificates: []tls.Certificate{cert},
		NextProtos:   []string{"ark-quic-v1"},
	}, nil
}

// GenerateDevTLSConfig builds an in-memory, self-signed ECDSA cert for
// local dev/testing only — never call this in a real deployment.
// arkmultitool's Http3Publisher pins the relay's real certificate (or its
// CA) explicitly; a fresh self-signed cert on every process start has
// nothing stable to pin against, and any MITM defeats it trivially. This
// exists purely so `docker compose up` and local manual testing don't
// require generating and wiring a cert file just to exercise the QUIC path
// — see cmd/relay/main.go for the loud warning printed when this path is
// taken.
func GenerateDevTLSConfig() (*tls.Config, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("quicserver: generate dev key: %w", err)
	}

	serial, err := rand.Int(rand.Reader, big.NewInt(1<<62))
	if err != nil {
		return nil, fmt.Errorf("quicserver: generate serial: %w", err)
	}

	template := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: "ark_relay-dev"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		IsCA:         true,
		DNSNames:     []string{"localhost"},
	}

	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		return nil, fmt.Errorf("quicserver: create dev certificate: %w", err)
	}

	cert := tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}
	return &tls.Config{
		Certificates: []tls.Certificate{cert},
		NextProtos:   []string{"ark-quic-v1"},
	}, nil
}
