// Command relay runs ark_relay: the live WebSocket fan-out of ESP
// sightings between clients of the same tribe, per telemetry-api-v1.md §7.
// It holds no durable state of its own — every sighting it relays is also
// written to the shared Redis instance ark_backend reads (§8.1); Postgres
// is never touched from here.
package main

import (
	"context"
	"crypto/tls"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"

	"ark_relay/internal/authjwt"
	"ark_relay/internal/config"
	"ark_relay/internal/hub"
	"ark_relay/internal/quicserver"
	"ark_relay/internal/revocation"
	"ark_relay/internal/store"
	"ark_relay/internal/streamproducer"
	"ark_relay/internal/wsserver"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))

	if err := run(log); err != nil {
		log.Error("fatal", "err", err)
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}

	verifier, err := authjwt.LoadVerifier(cfg.JWTPublicKeyPath)
	if err != nil {
		return err
	}

	rdb := redis.NewClient(&redis.Options{
		Addr:     cfg.RedisAddr,
		Password: cfg.RedisPassword,
		DB:       cfg.RedisDB,
	})
	defer rdb.Close()

	startupCtx, cancelStartup := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancelStartup()
	if err := store.Ping(startupCtx, rdb); err != nil {
		return err
	}

	es := store.NewEntityStore(rdb, cfg.EntityTTL)
	members := store.NewGroupMembership(rdb)
	sp := streamproducer.New(rdb)
	h := hub.New(log)
	handler := wsserver.New(h, verifier, es, es, sp, members, log,
		cfg.PingInterval, cfg.PongWait, cfg.WriteWait, cfg.MaxEntitiesPerMessage, cfg.MaxMessageBytes)

	revocationCtx, stopRevocation := context.WithCancel(context.Background())
	defer stopRevocation()
	go revocation.Watch(revocationCtx, rdb, h, log)

	quicCtx, stopQUIC := context.WithCancel(context.Background())
	defer stopQUIC()
	quicErr := make(chan error, 1)
	if cfg.QUICListenAddr != "" {
		tlsConf, err := loadOrGenerateQUICTLS(cfg, log)
		if err != nil {
			return err
		}
		qs := quicserver.New(h, verifier, es, es, sp, members, log,
			cfg.PingInterval, cfg.PongWait, cfg.WriteWait, cfg.MaxEntitiesPerMessage, cfg.MaxMessageBytes)
		go func() {
			log.Info("ark_relay QUIC listening", "addr", cfg.QUICListenAddr)
			quicErr <- qs.ListenAndServe(quicCtx, cfg.QUICListenAddr, tlsConf)
		}()
	}

	mux := http.NewServeMux()
	mux.Handle("/v1/relay/ws", handler)
	// AsyncAPI-спека + web-viewer — статика, не код: правится руками
	// вместе с internal/protocol/message.go, никакого автогена здесь нет.
	mux.Handle("/docs/", http.StripPrefix("/docs/", http.FileServer(http.Dir(cfg.DocsDir))))
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		if err := rdb.Ping(r.Context()).Err(); err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	})

	srv := &http.Server{
		Addr:              cfg.ListenAddr,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}

	serveErr := make(chan error, 1)
	go func() {
		log.Info("ark_relay listening", "addr", cfg.ListenAddr)
		serveErr <- srv.ListenAndServe()
	}()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	select {
	case err := <-serveErr:
		stopQUIC()
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			return err
		}
		return nil
	case err := <-quicErr:
		if err != nil {
			return err
		}
		return nil
	case <-ctx.Done():
		log.Info("shutting down")
		stopQUIC() // unblocks quicserver's Accept loop the same way srv.Shutdown does for HTTP below
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		// Stop accepting new connections and let in-flight ones finish
		// their close handshake before the process exits — the WS
		// upgrader has already handed connections to hub.Client.Run, so
		// Shutdown here only stops new HTTP/upgrade traffic; existing
		// sockets close on their own read/write errors or client-driven
		// close, which is acceptable for a best-effort relay.
		if err := srv.Shutdown(shutdownCtx); err != nil {
			return err
		}
		return nil
	}
}

// loadOrGenerateQUICTLS loads a real cert/key pair when configured, or
// falls back to an ephemeral self-signed dev cert when neither is set
// (config.Load already rejects the case where only one of the two is set)
// — loud on purpose: this fallback is never correct for a real deployment.
func loadOrGenerateQUICTLS(cfg config.Config, log *slog.Logger) (*tls.Config, error) {
	if cfg.TLSCertFile != "" {
		return quicserver.LoadTLSConfig(cfg.TLSCertFile, cfg.TLSKeyFile)
	}
	log.Warn("RELAY_TLS_CERT_FILE not set: generating an ephemeral self-signed QUIC certificate for THIS PROCESS ONLY — never valid for a real deployment, arkmultitool clients have nothing stable to pin against")
	return quicserver.GenerateDevTLSConfig()
}
