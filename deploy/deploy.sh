#!/usr/bin/env bash
# Bring-up script for a fresh Ubuntu VPS: installs Docker if missing,
# generates every secret this stack needs on first run (.env, JWT RS256
# keypair, QUIC TLS keypair), then builds and starts the compose stack
# (postgres, redis, backend_python, backend_go).
#
# Idempotent: safe to re-run after a `git pull` to rebuild/redeploy --
# it never overwrites an existing .env or an existing keypair, only fills
# in what's missing.
#
# DB seed data: alembic upgrade head runs automatically on every
# backend_python container start (see backend_python/docker-entrypoint.sh)
# and already seeds every lookup table (account_status, admin_role,
# api_key_status, ...) -- migrations/versions/93868d516e83_add_auth_and_ark_schema.py.
# Nothing to do here for that. The one seed this script CANNOT do for you
# is the first admin account -- scripts/create_admin.py prompts for a
# password interactively on purpose (see its own doc comment: a password
# on the command line ends up in `ps aux` and shell history for every user
# on the box). This script prints the exact command to run for that as
# its last step.
#
# Usage:
#   ssh you@vps
#   git clone <repo-url> ark-backend && cd ark-backend
#   ./deploy/deploy.sh
#
#   # or, if you already have the repo on the VPS:
#   cd backend && ./deploy/deploy.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$1" >&2; exit 1; }

[[ -f docker-compose.yml ]] || die "run this from the backend/ checkout (docker-compose.yml not found next to deploy/)"

# --- 1. Docker + Compose plugin ------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "Docker not found -- installing via get.docker.com"
  curl -fsSL https://get.docker.com | sh
  if [[ -n "${SUDO_USER:-}" ]]; then
    usermod -aG docker "$SUDO_USER" || true
    warn "added $SUDO_USER to the docker group -- log out/in (or newgrp docker) for it to take effect"
  fi
else
  log "Docker already installed: $(docker --version)"
fi

if ! docker compose version >/dev/null 2>&1; then
  die "docker compose plugin missing even after install -- check https://docs.docker.com/compose/install/linux/"
fi

# --- 2. .env: fill in real secrets, never touch an existing file ---------
if [[ ! -f .env ]]; then
  log "No .env found -- generating one from .env.example with real secrets"
  cp .env.example .env
  # openssl over `python3 -c secrets...`: this runs before backend_python's
  # own image (and its Python) necessarily exists yet, openssl ships on
  # every Ubuntu base image.
  gen_secret() { openssl rand -base64 48 | tr -d '\n=+/' | head -c 64; }
  sed -i "s#^APP_SECRET_KEY=.*#APP_SECRET_KEY=$(gen_secret)#" .env
  sed -i "s#^SECURITY_PEPPER=.*#SECURITY_PEPPER=$(gen_secret)#" .env
  sed -i "s#^DB_PASSWORD=.*#DB_PASSWORD=$(gen_secret)#" .env
  log "Generated .env -- back this file up (it holds the DB password and the pepper; losing the pepper invalidates every stored password hash)"
else
  log ".env already exists -- leaving it alone"
fi

# --- 3. JWT RS256 keypair (account/admin tokens, verified by both --------
#        backend_python and backend_go off the same public key) ----------
mkdir -p keys
if [[ ! -f keys/jwt_private.pem || ! -f keys/jwt_public.pem ]]; then
  log "Generating JWT RS256 keypair (keys/jwt_{private,public}.pem)"
  openssl genrsa -out keys/jwt_private.pem 2048 2>/dev/null
  openssl rsa -in keys/jwt_private.pem -pubout -out keys/jwt_public.pem 2>/dev/null
  chmod 600 keys/jwt_private.pem
else
  log "JWT keypair already present -- leaving it alone (rotating it invalidates every issued token)"
fi

# --- 4. QUIC TLS keypair for backend_go (arkmultitool's Http3Publisher) --
#        A real, long-lived self-signed cert beats cmd/relay's own default
#        (a brand new ephemeral one on every restart, see its startup log
#        warning) -- clients at least have something stable to eventually
#        pin against. Not a CA-signed cert: QUIC/UDP direct-to-VPS has no
#        HTTP-01 challenge path without fronting it with a domain + a
#        second listener, out of scope here.
if [[ ! -f keys/relay_cert.pem || ! -f keys/relay_key.pem ]]; then
  log "Generating a long-lived self-signed QUIC TLS keypair (keys/relay_{cert,key}.pem)"
  read -rp "Public hostname or IP clients will connect to (CN for the cert; blank = server IP autodetect): " RELAY_CN
  if [[ -z "$RELAY_CN" ]]; then
    RELAY_CN="$(curl -fsSL -4 https://ifconfig.me 2>/dev/null || echo "127.0.0.1")"
    log "Using detected public IP as CN: $RELAY_CN"
  fi
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout keys/relay_key.pem -out keys/relay_cert.pem \
    -subj "/CN=${RELAY_CN}" 2>/dev/null
  chmod 600 keys/relay_key.pem
  # backend_go's image is gcr.io/distroless/static-debian12:nonroot -- the
  # relay process inside the container runs as UID/GID 65532, not root.
  # Left root:root (deploy.sh normally runs as root/sudo), a 600 key is
  # unreadable to that UID and the container crash-loops on startup
  # ("load cert/key: ... permission denied"). backend_python's jwt_private.pem
  # doesn't need this -- that image has no USER directive, runs as root.
  if ! chown 65532:65532 keys/relay_key.pem 2>/dev/null; then
    warn "could not chown keys/relay_key.pem to 65532:65532 (not running as root?) -- backend_go will crash-loop with 'permission denied' until you run: sudo chown 65532:65532 keys/relay_key.pem"
  fi
  if ! grep -q '^RELAY_TLS_CERT_FILE=' .env; then
    {
      echo "RELAY_TLS_CERT_FILE=/keys/relay_cert.pem"
      echo "RELAY_TLS_KEY_FILE=/keys/relay_key.pem"
    } >> .env
  fi
else
  log "QUIC TLS keypair already present -- leaving it alone"
fi

# --- 5. Build and start the stack -----------------------------------------
log "Building images and starting the stack"
docker compose up -d --build

# --- 6. Wait for backend_python to report healthy -------------------------
log "Waiting for backend_python to become healthy (runs alembic upgrade head on startup)"
status=""
for _ in $(seq 1 60); do
  container_id="$(docker compose ps -q backend_python)"
  if [[ -n "$container_id" ]]; then
    status="$(docker inspect -f '{{.State.Health.Status}}' "$container_id" 2>/dev/null || true)"
    [[ "$status" == "healthy" ]] && break
  fi
  sleep 2
done
if [[ "$status" != "healthy" ]]; then
  warn "backend_python did not report healthy in time (last status: ${status:-unknown}) -- check: docker compose logs backend_python"
else
  log "backend_python is healthy -- migrations applied, lookup tables seeded"
fi

docker compose ps

# --- 7. What's left: the one seed step that must stay interactive ---------
cat <<'EOF'

==> Stack is up. What's left:

  1. Create the first admin account (password prompted interactively,
     never as a CLI argument -- see scripts/create_admin.py):

       docker compose exec backend_python python /app/scripts/create_admin.py \
         --username root --role superadmin

  2. Open firewall ports for the services clients actually need to reach
     (postgres/redis are NOT published outside the docker network -- leave
     them closed):

       sudo ufw allow 8000/tcp   # backend_python API
       sudo ufw allow 8081/tcp   # backend_go AsyncAPI docs + healthz
       sudo ufw allow 8443/udp   # backend_go QUIC (arkmultitool sharing)

  3. Point clients at this VPS instead of localhost:
       - arkmultitool inject: kopt_injector.exe ... --backend <this-host>:8443
       - anything hitting backend_python's HTTP API directly: http://<this-host>:8000

  4. .env, keys/jwt_private.pem, keys/relay_key.pem are the whole trust
     base of this deployment -- back them up somewhere that isn't this VPS.
EOF
