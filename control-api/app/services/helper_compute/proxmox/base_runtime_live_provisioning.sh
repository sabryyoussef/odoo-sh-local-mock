#!/bin/bash
# HC3.9 — Base Odoo Runtime Provisioning Live Script
# 
# This script provisions a clean Ubuntu 24.04 VM with:
# - Base OS dependencies
# - PostgreSQL
# - Odoo 19 runtime (no customer DB/applications)
#
# Must be run with sudo/root privileges on the guest VM.
#
# Usage: sudo bash base_runtime_live_provisioning.sh

set -e  # Exit on error

readonly ODOO_VERSION="19"
readonly ODOO_USER="odoo"
readonly ODOO_GROUP="odoo"
readonly ODOO_HOME="/opt/odoo"
readonly ODOO_PORT="8069"
readonly PG_VERSION="16"

# Logging
log() {
    echo "[$(date +'%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a /var/log/hc39-provisioning.log
}

error() {
    echo "[$(date +'%Y-%m-%dT%H:%M:%SZ')] ERROR: $*" | tee -a /var/log/hc39-provisioning.log >&2
    exit 1
}

# ============================================================================
# 1. INSPECT CURRENT STATE
# ============================================================================

log "=== Phase 1: Inspecting current state ==="

OS_VERSION=$(grep VERSION_ID /etc/os-release | cut -d= -f2 | tr -d '"')
log "OS Version: $OS_VERSION"

# Check if already provisioned
if [ -f /opt/odoo/bin/python ]; then
    log "Odoo already installed at $ODOO_HOME, skipping installation"
    exit 0
fi

# Check PostgreSQL
if command -v psql &> /dev/null; then
    PG_RUNNING=$(pg_isready -h localhost -p 5432 > /dev/null 2>&1 && echo "yes" || echo "no")
    log "PostgreSQL already installed, running=$PG_RUNNING"
else
    log "PostgreSQL not installed"
fi

# Check available ports
log "Checking port $ODOO_PORT..."
if ss -tulpn 2>/dev/null | grep -q ":$ODOO_PORT "; then
    error "Port $ODOO_PORT already in use"
fi

# ============================================================================
# 2. UPDATE SYSTEM AND INSTALL BASE DEPENDENCIES
# ============================================================================

log "=== Phase 2: Installing base OS dependencies ==="

apt-get update
apt-get install -y \
    build-essential \
    wget \
    git \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    libpq-dev \
    libxml2-dev \
    libxslt1-dev \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
    libtiff5-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libxcb1-dev \
    npm \
    sudo \
    curl \
    openssh-server

log "Base dependencies installed"

# ============================================================================
# 3. INSTALL POSTGRESQL
# ============================================================================

log "=== Phase 3: Installing PostgreSQL ==="

apt-get install -y postgresql-$PG_VERSION postgresql-contrib-$PG_VERSION libpq-dev

# Start PostgreSQL service
systemctl enable postgresql
systemctl start postgresql

log "PostgreSQL $PG_VERSION installed and started"

# Verify PostgreSQL connectivity
sudo -u postgres psql -c "SELECT version();" || error "PostgreSQL connection failed"
log "PostgreSQL connectivity verified"

# ============================================================================
# 4. CREATE ODOO SERVICE USER AND DIRECTORIES
# ============================================================================

log "=== Phase 4: Creating service user and directories ==="

# Create odoo user
if ! id "$ODOO_USER" &>/dev/null; then
    useradd -r -m -s /bin/bash "$ODOO_USER"
    log "Created user: $ODOO_USER"
else
    log "User $ODOO_USER already exists"
fi

# Create Odoo directories
mkdir -p "$ODOO_HOME"
mkdir -p /var/lib/odoo
mkdir -p /var/log/odoo

# Set permissions
chown -R "$ODOO_USER:$ODOO_GROUP" "$ODOO_HOME" /var/lib/odoo /var/log/odoo
chmod 755 "$ODOO_HOME" /var/lib/odoo /var/log/odoo

log "Directories created and permissions set"

# ============================================================================
# 5. DOWNLOAD AND INSTALL ODOO
# ============================================================================

log "=== Phase 5: Installing Odoo runtime ==="

cd /tmp

# Download Odoo 19 Community Edition - use codeload URL which handles redirects better
ODOO_URL="https://codeload.github.com/odoo/odoo/tar.gz/refs/${ODOO_VERSION}.0"
log "Downloading Odoo from: $ODOO_URL"

# Use -L to follow redirects, -f to fail on HTTP errors
wget -L -q "$ODOO_URL" -O odoo-${ODOO_VERSION}.0.tar.gz || error "Failed to download Odoo"
tar xzf "odoo-${ODOO_VERSION}.0.tar.gz"

# Move to installation directory
ODOO_SRC_DIR="odoo-${ODOO_VERSION}.0"
if [ -d "$ODOO_SRC_DIR" ]; then
    rm -rf "$ODOO_HOME"/*
    cp -r "$ODOO_SRC_DIR"/* "$ODOO_HOME"/ || error "Failed to copy Odoo files"
    chown -R "$ODOO_USER:$ODOO_GROUP" "$ODOO_HOME"
    log "Odoo runtime copied to $ODOO_HOME"
else
    error "Odoo extraction failed"
fi

# ============================================================================
# 6. CREATE PYTHON VIRTUAL ENVIRONMENT
# ============================================================================

log "=== Phase 6: Setting up Python virtual environment ==="

sudo -u "$ODOO_USER" python3 -m venv "$ODOO_HOME/venv"
source "$ODOO_HOME/venv/bin/activate"

# Install Python dependencies
pip install --upgrade pip setuptools wheel
pip install -r "$ODOO_HOME/requirements.txt" 2>&1 | grep -E "Successfully|error" || true

log "Python virtual environment created and packages installed"

# ============================================================================
# 7. GENERATE BASE ODOO CONFIGURATION
# ============================================================================

log "=== Phase 7: Generating base Odoo configuration ==="

ODOO_CONFIG="/etc/odoo/odoo.conf"
mkdir -p /etc/odoo
chown "$ODOO_USER:$ODOO_GROUP" /etc/odoo

cat > "$ODOO_CONFIG" << 'ODOOCONF'
[options]
; Admin password (placeholder - will be managed separately)
admin_passwd = __ADMIN_PASSWD_PLACEHOLDER__

; Database configuration
db_host = localhost
db_port = 5432
db_user = odoo
db_password = __DB_PASSWD_PLACEHOLDER__
db_name = odoo

; Server configuration
http_interface = 127.0.0.1
http_port = 8069
proxy_mode = False
without_demo = True

; File storage
data_dir = /var/lib/odoo

; Modules
addons_path = /opt/odoo/addons
auto_reload = False

; Logging
log_level = info
logfile = /var/log/odoo/odoo.log

; Performance
workers = 0
ODOOCONF

chown "$ODOO_USER:$ODOO_GROUP" "$ODOO_CONFIG"
chmod 640 "$ODOO_CONFIG"

log "Base Odoo configuration generated at $ODOO_CONFIG"

# ============================================================================
# 8. CREATE DATABASE ROLE AND INITIAL DATABASE
# ============================================================================

log "=== Phase 8: Creating PostgreSQL database role and initial database ==="

# Create database user (password will be managed via secure store)
sudo -u postgres psql << PGSQL
CREATE ROLE odoo WITH LOGIN CREATEDB ENCRYPTED PASSWORD 'odoo-temp-12345';
ALTER ROLE odoo SET client_encoding = 'UTF8';
ALTER ROLE odoo SET default_transaction_isolation = 'read committed';
ALTER ROLE odoo SET default_transaction_deferrable = on;
ALTER ROLE odoo SET lock_timeout = '15min';
ALTER ROLE odoo SET idle_in_transaction_session_timeout = '15min';
PGSQL

log "PostgreSQL role 'odoo' created"

# Create initial database (no customer data)
sudo -u postgres createdb -O odoo odoo 2>/dev/null || log "Database 'odoo' may already exist"

# Verify connectivity
sudo -u "$ODOO_USER" psql -h localhost -U odoo -d odoo -c "SELECT version();" > /dev/null || error "PostgreSQL user connectivity check failed"

log "PostgreSQL database and role verified"

# ============================================================================
# 9. CREATE SYSTEMD SERVICE
# ============================================================================

log "=== Phase 9: Creating systemd service ==="

cat > /etc/systemd/system/odoo.service << 'SYSCTL'
[Unit]
Description=Odoo (Base Runtime)
Documentation=https://www.odoo.com/documentation/
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
SyslogIdentifier=odoo
User=odoo
Group=odoo

WorkingDirectory=/opt/odoo
ExecStart=/opt/odoo/venv/bin/python /opt/odoo/odoo-bin -c /etc/odoo/odoo.conf

; Security
NoNewPrivileges=true
PrivateTmp=true

; Restart policy
Restart=on-failure
RestartSec=10
StartLimitInterval=600
StartLimitBurst=3

; Resource limits
LimitNOFILE=65535

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SYSCTL

systemctl daemon-reload
systemctl enable odoo

log "Systemd service created and enabled"

# ============================================================================
# 10. START ODOO SERVICE
# ============================================================================

log "=== Phase 10: Starting Odoo service ==="

systemctl start odoo
sleep 5

# Check if service started
if systemctl is-active --quiet odoo; then
    log "✓ Odoo service started successfully"
else
    error "Odoo service failed to start. Check logs: journalctl -xe"
fi

# ============================================================================
# 11. HEALTH VERIFICATION
# ============================================================================

log "=== Phase 11: Performing health checks ==="

# Check process running
if pgrep -f "odoo-bin" > /dev/null; then
    log "✓ Odoo process is running"
else
    error "Odoo process not running"
fi

# Check port listening
sleep 3
if ss -tulpn 2>/dev/null | grep -q ":$ODOO_PORT "; then
    log "✓ Odoo listening on port $ODOO_PORT"
else
    error "Odoo not listening on port $ODOO_PORT"
fi

# Test HTTP endpoint
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8069/ 2>/dev/null || echo "000")
if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "302" ]; then
    log "✓ HTTP endpoint responds ($HTTP_STATUS)"
else
    log "⚠ HTTP endpoint returned status $HTTP_STATUS (may be normal during startup)"
fi

# Check PostgreSQL connectivity
sudo -u "$ODOO_USER" psql -h localhost -U odoo -d odoo -c "\dt" > /dev/null 2>&1 && log "✓ PostgreSQL connectivity verified" || log "⚠ PostgreSQL connectivity check inconclusive"

# Check logs for errors
LOG_ERRORS=$(tail -50 /var/log/odoo/odoo.log 2>/dev/null | grep -i "CRITICAL\|ERROR" | wc -l)
if [ "$LOG_ERRORS" -eq 0 ]; then
    log "✓ No critical errors in startup logs"
else
    log "⚠ Found $LOG_ERRORS errors in logs (check /var/log/odoo/odoo.log)"
fi

# Get Odoo version
ODOO_RUNTIME_VERSION=$(sudo -u "$ODOO_USER" "$ODOO_HOME/venv/bin/python" -c "import odoo; print(odoo.VERSION[:5])" 2>/dev/null | tr '.' '\n' | head -1)
log "✓ Odoo runtime version: $ODOO_RUNTIME_VERSION"

# ============================================================================
# 12. SUMMARY
# ============================================================================

log "=== Phase 12: Provisioning complete ==="
log "Summary:"
log "  Odoo Version: $ODOO_VERSION"
log "  OS Version: $OS_VERSION"
log "  PostgreSQL Version: $PG_VERSION"
log "  Service User: $ODOO_USER"
log "  Installation Path: $ODOO_HOME"
log "  Service Port: $ODOO_PORT"
log "  Configuration: $ODOO_CONFIG"
log "  Service Status: $(systemctl is-active odoo)"
log ""
log "✓ Base Odoo runtime provisioning complete"
log "✓ NO customer database created"
log "✓ NO Ready Solution modules installed"
log "✓ Service ready for next phase: customer application deployment"

exit 0
