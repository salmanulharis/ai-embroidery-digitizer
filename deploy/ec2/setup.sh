#!/usr/bin/env bash
# Embroidery Digitizer - server install / update for Ubuntu 24.04 LTS (AWS EC2).
# Normally run by GitHub Actions (.github/workflows/deploy.yml) on every release tag.
# Idempotent: installs what is missing, deploys the code, writes settings, restarts,
# checks health and rolls back to the previous release if the new one does not start.
#
# Inputs (environment variables, each one set separately - in CI from GitHub vars/secrets):
#   SOURCE_DIR           folder with the release code (required)
#   SETTINGS_FILE        optional file of "export KEY=value" lines; read, then securely deleted
#   DOMAIN, EMAIL        site address (HTTPS via Let's Encrypt) and certificate email
#   BASIC_AUTH_USER, BASIC_AUTH_PASSWORD   optional site password
#   RELEASE_VERSION      shown in logs (the git tag)
#   App settings: MAX_UPLOAD_MB RASTER_MAX_SIDE_PX RASTER_PX_PER_MM AI_TIMEOUT_S AI_ALLOW_PRIVATE_ENDPOINTS
#   Accounts:     SUPER_ADMIN_EMAIL SUPER_ADMIN_PASSWORD (first admin), APP_SECRET_KEY, SESSION_DAYS
#   AI (optional):  GEMINI_* OPENAI_* OPENROUTER_* ANTHROPIC_* CUSTOM_AI_*  (see SETTING_KEYS)
#   SKIP_SYSTEMD=1       prepare files only (container tests)
set -euo pipefail

if [[ -n "${SETTINGS_FILE:-}" && -f "$SETTINGS_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  . "$SETTINGS_FILE"
  set +a
  shred -u "$SETTINGS_FILE" 2>/dev/null || rm -f "$SETTINGS_FILE"
fi

SOURCE_DIR="${SOURCE_DIR:-}"
DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-}"
BASIC_AUTH_USER="${BASIC_AUTH_USER:-}"
BASIC_AUTH_PASSWORD="${BASIC_AUTH_PASSWORD:-}"
RELEASE_VERSION="${RELEASE_VERSION:-manual}"
SKIP_SYSTEMD="${SKIP_SYSTEMD:-0}"

APP_USER=embroidery
APP_DIR=/opt/embroidery
PREV_DIR=/opt/embroidery.prev
DATA_DIR=/var/lib/embroidery
ENV_DIR=/etc/embroidery
ENV_FILE="$ENV_DIR/embroidery.env"
PORT=8000
SETTING_KEYS=(
  MAX_UPLOAD_MB RASTER_MAX_SIDE_PX RASTER_PX_PER_MM AI_TIMEOUT_S AI_ALLOW_PRIVATE_ENDPOINTS
  SUPER_ADMIN_EMAIL SUPER_ADMIN_PASSWORD APP_SECRET_KEY SESSION_DAYS
  GEMINI_API_KEY GEMINI_IMAGE_MODEL GEMINI_VISION_MODEL
  OPENAI_API_KEY OPENAI_IMAGE_MODEL OPENAI_VISION_MODEL
  OPENROUTER_API_KEY OPENROUTER_IMAGE_MODEL OPENROUTER_VISION_MODEL
  ANTHROPIC_API_KEY ANTHROPIC_VISION_MODEL
  CUSTOM_AI_BASE_URL CUSTOM_AI_API_KEY CUSTOM_AI_VISION_MODEL CUSTOM_AI_IMAGE_MODEL
)

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
as_app() { runuser -u "$APP_USER" -- env HOME="$APP_DIR" "$@"; }

[[ $EUID -eq 0 ]] || die "Run with sudo"
[[ -n "$SOURCE_DIR" && -f "$SOURCE_DIR/requirements.txt" ]] || die "SOURCE_DIR must point to the release code"
[[ -z "$BASIC_AUTH_USER" || -n "$BASIC_AUTH_PASSWORD" ]] || die "BASIC_AUTH_PASSWORD is required with BASIC_AUTH_USER"
log "Deploying release $RELEASE_VERSION"

# --------------------------------------------------------------------------- system packages (only if missing)
PACKAGES=(python3 python3-venv python3-pip curl ca-certificates gnupg debian-keyring debian-archive-keyring
          apt-transport-https unattended-upgrades rsync fail2ban)
missing=()
for p in "${PACKAGES[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
export DEBIAN_FRONTEND=noninteractive
if (( ${#missing[@]} )); then
  log "Installing system packages: ${missing[*]}"
  apt-get update -q
  apt-get install -y -q --no-install-recommends "${missing[@]}"
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' || die "Python 3.12+ required (use Ubuntu 24.04)"
if ! command -v caddy >/dev/null; then
  log "Installing Caddy (web server with automatic HTTPS)"
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt -o /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
  apt-get install -y -q caddy
fi

add_swap() {  # best effort: never block a release because swap is unavailable
  { fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048; } 2>/dev/null &&
    chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile || return 1
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo 'vm.swappiness=10' > /etc/sysctl.d/90-embroidery-swap.conf
  sysctl -q -p /etc/sysctl.d/90-embroidery-swap.conf || true
}
if [[ "$SKIP_SYSTEMD" != "1" ]] && ! swapon --show 2>/dev/null | grep -q /swapfile; then
  log "Adding 2 GB swap"
  add_swap || { echo "Warning: swap could not be enabled - continuing without it."; rm -f /swapfile; }
fi

# --------------------------------------------------------------------------- code
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$APP_DIR"
install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$DATA_DIR"
install -d -o root -g root -m 700 "$ENV_DIR"

RSYNC_CODE=(rsync -a --delete --exclude .git --exclude .github --exclude .venv --exclude data --exclude .env
            --exclude __pycache__ --exclude .pytest_cache)
if [[ -f "$APP_DIR/requirements.txt" ]]; then
  log "Keeping a copy of the current release for rollback"
  "${RSYNC_CODE[@]}" "$APP_DIR"/ "$PREV_DIR"/
fi
log "Installing release code"
"${RSYNC_CODE[@]}" "$SOURCE_DIR"/ "$APP_DIR"/
echo "$RELEASE_VERSION" > "$APP_DIR/RELEASE"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

install_python() {
  [[ -x "$APP_DIR/.venv/bin/python" ]] || as_app python3 -m venv "$APP_DIR/.venv"
  as_app "$APP_DIR/.venv/bin/python" -m pip install -q --upgrade pip
  as_app "$APP_DIR/.venv/bin/python" -m pip install -q -r "$APP_DIR/requirements.txt"
  as_app "$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/embroidery_app" >/dev/null
}
log "Installing Python packages"
install_python

# --------------------------------------------------------------------------- settings (rewritten every deploy)
log "Writing settings"
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
: "${MAX_UPLOAD_MB:=40}"
: "${AI_TIMEOUT_S:=120}"
: "${AI_ALLOW_PRIVATE_ENDPOINTS:=0}"
[[ -n "${RASTER_MAX_SIDE_PX:-}" ]] || RASTER_MAX_SIDE_PX=$([[ $MEM_MB -lt 1600 ]] && echo 1400 || echo 2000)
umask 077
{
  echo "# Generated by deploy/ec2/setup.sh ($RELEASE_VERSION) - managed by GitHub Actions, edits are overwritten."
  echo "DATA_DIR=$DATA_DIR"
  echo "PYTHONDONTWRITEBYTECODE=1"
  echo "RELEASE_VERSION=$RELEASE_VERSION"
  for key in "${SETTING_KEYS[@]}"; do
    value="${!key:-}"
    [[ -z "$value" ]] && continue
    [[ "$value" == *$'\n'* ]] && die "Setting $key must be a single line"
    printf '%s=%s\n' "$key" "$value"
  done
} > "$ENV_FILE"
umask 022
chmod 600 "$ENV_FILE"   # read by systemd as root before the app starts; the app user cannot read it

# --------------------------------------------------------------------------- services
cat > /etc/systemd/system/embroidery.service <<EOF
[Unit]
Description=Embroidery Digitizer
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
# One worker on purpose: projects and the stitch cache live on this server.
ExecStart=$APP_DIR/.venv/bin/uvicorn embroidery_app.api:app --host 127.0.0.1 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=always
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$DATA_DIR
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/embroidery-cleanup.service <<EOF
[Unit]
Description=Delete Embroidery Digitizer projects older than 7 days

[Service]
Type=oneshot
User=$APP_USER
ExecStart=/usr/bin/find $DATA_DIR/projects -mindepth 1 -maxdepth 1 -type d -mtime +7 -exec rm -rf {} +
EOF

cat > /etc/systemd/system/embroidery-cleanup.timer <<EOF
[Unit]
Description=Daily cleanup of old Embroidery Digitizer projects

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF

# --------------------------------------------------------------------------- Caddy
SITE="${DOMAIN:-:80}"
AUTH_BLOCK=""
if [[ -n "$BASIC_AUTH_USER" ]]; then
  HASH=$(caddy hash-password --plaintext "$BASIC_AUTH_PASSWORD")
  AUTH_BLOCK="	basic_auth {
		$BASIC_AUTH_USER $HASH
	}"
fi
HSTS=""
[[ -n "$DOMAIN" ]] && HSTS='		Strict-Transport-Security "max-age=31536000"'
{
  [[ -n "$EMAIL" ]] && printf '{\n\temail %s\n}\n\n' "$EMAIL"
  cat <<EOF
$SITE {
	encode zstd gzip
	request_body {
		max_size 50MB
	}
$AUTH_BLOCK
	header {
$HSTS
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
		Referrer-Policy "no-referrer"
		-Server
	}
	reverse_proxy 127.0.0.1:$PORT {
		transport http {
			read_timeout 10m
			write_timeout 10m
		}
	}
}
EOF
} > /etc/caddy/Caddyfile
chmod 640 /etc/caddy/Caddyfile && chgrp caddy /etc/caddy/Caddyfile 2>/dev/null || true
caddy fmt --overwrite /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null

if [[ "$SKIP_SYSTEMD" == "1" ]]; then log "SKIP_SYSTEMD=1: files prepared, services not started"; exit 0; fi

# --------------------------------------------------------------------------- start, check, roll back on failure
healthy() {
  for _ in $(seq 1 30); do
    curl -fsS "http://127.0.0.1:$PORT/api/config" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}
systemctl daemon-reload
systemctl enable embroidery.service caddy >/dev/null 2>&1
systemctl enable --now embroidery-cleanup.timer fail2ban >/dev/null 2>&1
systemctl restart embroidery.service
systemctl reload caddy 2>/dev/null || systemctl restart caddy

log "Health check"
if healthy; then
  echo "Release $RELEASE_VERSION is live${DOMAIN:+ at https://$DOMAIN}."
  exit 0
fi
journalctl -u embroidery -n 60 --no-pager || true
if [[ -f "$PREV_DIR/requirements.txt" ]]; then
  log "New release did not start - rolling back to $(cat "$PREV_DIR/RELEASE" 2>/dev/null || echo previous)"
  "${RSYNC_CODE[@]}" "$PREV_DIR"/ "$APP_DIR"/
  chown -R "$APP_USER:$APP_USER" "$APP_DIR"
  install_python
  systemctl restart embroidery.service
  healthy && die "Release $RELEASE_VERSION failed; previous release restored." || die "Rollback also failed."
fi
die "Release $RELEASE_VERSION failed to start."
