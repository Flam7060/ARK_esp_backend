// Package config loads ark_relay's runtime configuration from environment
// variables. All environment-dependent values (addresses, TTLs, limits) live
// here — never as literals in the hot path — so retuning them is a restart,
// not a rebuild.
package config

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

// Config is the fully-resolved, validated runtime configuration for one
// ark_relay process.
type Config struct {
	// ListenAddr is the TCP address the WebSocket HTTP server binds to.
	ListenAddr string

	// QUICListenAddr is the UDP address the QUIC transport
	// (internal/quicserver) binds to — arkmultitool's Http3Publisher talks
	// to this, not ListenAddr. Empty disables the QUIC listener entirely
	// (e.g. a deployment not ready to open a UDP port yet); WS keeps
	// working either way — QUIC is an additional path, not a replacement.
	QUICListenAddr string

	// TLSCertFile/TLSKeyFile are the PEM cert/key QUIC serves — QUIC
	// mandates TLS 1.3, unlike ListenAddr's plain HTTP (TLS terminated by
	// a reverse proxy in front, if any). Both empty is valid for local
	// dev/testing only: cmd/relay generates an ephemeral, unlogged,
	// self-signed cert in that case and says so loudly — never do this in
	// a real deployment, arkmultitool's client would have nothing valid to
	// verify against anyway.
	TLSCertFile string
	TLSKeyFile  string

	// RedisAddr/RedisDB target the *same* Redis instance ark_backend reads
	// from for its "live map" view — see telemetry-api-v1.md §8.1. There is
	// deliberately one shared Redis, not one per service: the whole point of
	// §8 is a single source of truth for "now".
	RedisAddr     string
	RedisPassword string
	RedisDB       int

	// JWTPublicKeyPath points at the RSA public key (PEM, PKIX) used to
	// verify tokens ark_backend issued. The relay never needs the private
	// key — verification only (doc §7.3: "релею достаточно публичного ключа
	// в конфиге").
	JWTPublicKeyPath string

	// EntityTTL is how long a sighted entity survives in Redis without a
	// refresh before it silently expires from the "live" view. Doc §8.1:
	// 45-90x the client cadence (1-2s) so a lag spike doesn't flicker
	// entities off the map, but a real disappearance still clears in under
	// two minutes.
	EntityTTL time.Duration

	// PingInterval/PongWait govern the keepalive that detects dead
	// connections TCP itself won't report (crash, network loss without
	// FIN) — doc §7.3.
	PingInterval time.Duration
	PongWait     time.Duration
	WriteWait    time.Duration

	// MaxEntitiesPerMessage bounds a single "sighting" payload so a
	// compromised or buggy client can't force an unbounded Redis pipeline
	// per message.
	MaxEntitiesPerMessage int

	// MaxMessageBytes bounds the raw WebSocket frame size read from a
	// client connection.
	MaxMessageBytes int64

	// DocsDir serves the hand-maintained AsyncAPI spec + viewer at
	// /docs/*. Unlike backend_python's OpenAPI (generated from code on
	// every request), this is static content someone must update by hand
	// alongside internal/protocol/message.go — see docs/asyncapi.yaml.
	DocsDir string
}

// Load reads Config from the environment, applying defaults matching
// telemetry-api-v1.md §7-§8, and returns an error naming the first invalid
// or missing required value.
func Load() (Config, error) {
	cfg := Config{
		ListenAddr:            getEnv("RELAY_LISTEN_ADDR", ":8081"),
		QUICListenAddr:        getEnv("RELAY_QUIC_LISTEN_ADDR", ""),
		TLSCertFile:           getEnv("RELAY_TLS_CERT_FILE", ""),
		TLSKeyFile:            getEnv("RELAY_TLS_KEY_FILE", ""),
		RedisAddr:             getEnv("RELAY_REDIS_ADDR", "localhost:6379"),
		RedisPassword:         getEnv("RELAY_REDIS_PASSWORD", ""),
		JWTPublicKeyPath:      getEnv("RELAY_JWT_PUBLIC_KEY", ""),
		EntityTTL:             90 * time.Second,
		PingInterval:          30 * time.Second,
		PongWait:              60 * time.Second,
		WriteWait:             10 * time.Second,
		MaxEntitiesPerMessage: 500,
		MaxMessageBytes:       64 * 1024,
		DocsDir:               getEnv("RELAY_DOCS_DIR", "/docs"),
	}

	var err error
	if cfg.RedisDB, err = getEnvInt("RELAY_REDIS_DB", 0); err != nil {
		return Config{}, err
	}
	if cfg.EntityTTL, err = getEnvDuration("RELAY_ENTITY_TTL", cfg.EntityTTL); err != nil {
		return Config{}, err
	}
	if cfg.PingInterval, err = getEnvDuration("RELAY_PING_INTERVAL", cfg.PingInterval); err != nil {
		return Config{}, err
	}
	if cfg.PongWait, err = getEnvDuration("RELAY_PONG_WAIT", cfg.PongWait); err != nil {
		return Config{}, err
	}
	if cfg.MaxEntitiesPerMessage, err = getEnvInt("RELAY_MAX_ENTITIES_PER_MSG", cfg.MaxEntitiesPerMessage); err != nil {
		return Config{}, err
	}

	if cfg.JWTPublicKeyPath == "" {
		return Config{}, fmt.Errorf("config: RELAY_JWT_PUBLIC_KEY is required (path to RSA public key PEM)")
	}
	if cfg.PongWait <= cfg.PingInterval {
		return Config{}, fmt.Errorf("config: RELAY_PONG_WAIT (%s) must be greater than RELAY_PING_INTERVAL (%s)", cfg.PongWait, cfg.PingInterval)
	}
	if (cfg.TLSCertFile == "") != (cfg.TLSKeyFile == "") {
		return Config{}, fmt.Errorf("config: RELAY_TLS_CERT_FILE and RELAY_TLS_KEY_FILE must both be set, or both left empty")
	}

	return cfg, nil
}

func getEnv(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

func getEnvInt(key string, def int) (int, error) {
	v, ok := os.LookupEnv(key)
	if !ok || v == "" {
		return def, nil
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return 0, fmt.Errorf("config: %s=%q is not an integer: %w", key, v, err)
	}
	return n, nil
}

func getEnvDuration(key string, def time.Duration) (time.Duration, error) {
	v, ok := os.LookupEnv(key)
	if !ok || v == "" {
		return def, nil
	}
	d, err := time.ParseDuration(v)
	if err != nil {
		return 0, fmt.Errorf("config: %s=%q is not a duration: %w", key, v, err)
	}
	return d, nil
}
