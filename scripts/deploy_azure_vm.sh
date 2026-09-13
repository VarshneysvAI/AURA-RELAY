#!/usr/bin/env bash
# ==============================================================================
# AURA Relay — Automated Azure VM Provisioning & Deployment Script
# ==============================================================================
# Sets up AURA Relay on Ubuntu 22.04 / 24.04 LTS:
# - Installs Python 3.11+, Git, Playwright Chromium, system dependencies
# - Configures systemd daemon with automatic crash recovery
# - Configures continuous git auto-sync (polls GitHub every 30s for live updates)
# - Configures Nginx reverse proxy on port 80/443
# ==============================================================================

set -euo pipefail

echo "========================================================="
echo "   Starting AURA Relay Automated Azure VM Installation   "
echo "========================================================="

# 1. Update system & install dependencies
sudo apt-get update -y
sudo apt-get install -y \
    git \
    curl \
    wget \
    python3 \
    python3-pip \
    python3-venv \
    xvfb \
    dumb-init \
    nginx \
    certbot \
    python3-certbot-nginx

# 2. Clone or update repository in /opt/aura-relay
INSTALL_DIR="/opt/aura-relay"
if [ ! -d "$INSTALL_DIR" ]; then
    echo "Cloning AURA Relay repository..."
    sudo git clone https://github.com/VarshneysvAI/AURA-RELAY.git "$INSTALL_DIR"
else
    echo "Updating existing repository..."
    cd "$INSTALL_DIR"
    sudo git fetch origin main
    sudo git reset --hard origin/main
fi

cd "$INSTALL_DIR"
sudo chown -R $USER:$USER "$INSTALL_DIR"

# 3. Create virtual environment & install requirements
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Install Playwright browser and OS libraries
export PLAYWRIGHT_BROWSERS_PATH=0
python3 -m playwright install --with-deps chromium

# 5. Create runtime data folders
mkdir -p runtime/screenshots

# 6. Setup default .env if missing
if [ ! -f ".env" ]; then
    cp .env.example .env
    sed -i 's/HEADLESS=false/HEADLESS=true/g' .env
    sed -i 's/MODE=sandbox/MODE=live/g' .env
    sed -i 's/APP_HOST=127.0.0.1/APP_HOST=0.0.0.0/g' .env
    echo "Created default .env file in $INSTALL_DIR/.env"
fi

# 7. Create Systemd Service for AURA Relay
echo "Configuring aura-relay systemd service..."
sudo tee /etc/systemd/system/aura-relay.service > /dev/null <<EOF
[Unit]
Description=AURA Relay Autonomous AI Browser Coworker
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/.venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PLAYWRIGHT_BROWSERS_PATH=0"
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python main.py
Restart=always
RestartSec=5s

# Resource limits
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

# 8. Create Git Auto-Sync Script & Systemd Service
echo "Configuring continuous git auto-sync service..."
sudo tee "$INSTALL_DIR/scripts/git_sync_worker.sh" > /dev/null <<'EOF'
#!/usr/bin/env bash
cd /opt/aura-relay
git fetch origin main > /dev/null 2>&1 || true
LOCAL=$(git rev-parse HEAD 2>/dev/null || echo "")
REMOTE=$(git rev-parse origin/main 2>/dev/null || echo "")

if [ -n "$LOCAL" ] && [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
    echo "[GitSync] New changes detected on origin/main ($REMOTE). Updating..."
    git reset --hard origin/main
    export PLAYWRIGHT_BROWSERS_PATH=0
    /opt/aura-relay/.venv/bin/pip install -r requirements.txt --quiet || true
    /opt/aura-relay/.venv/bin/python -m playwright install chromium || true
    systemctl restart aura-relay
    echo "[GitSync] AURA Relay reloaded successfully with latest git commit."
fi
EOF
chmod +x "$INSTALL_DIR/scripts/git_sync_worker.sh"

sudo tee /etc/systemd/system/aura-git-sync.service > /dev/null <<EOF
[Unit]
Description=AURA Relay Git Auto-Sync Check
After=network.target

[Service]
Type=oneshot
User=$USER
ExecStart=$INSTALL_DIR/scripts/git_sync_worker.sh
EOF

sudo tee /etc/systemd/system/aura-git-sync.timer > /dev/null <<EOF
[Unit]
Description=Run AURA Relay Git Auto-Sync every 30 seconds

[Timer]
OnBootSec=10sec
OnUnitActiveSec=30sec
AccuracySec=1sec

[Install]
WantedBy=timers.target
EOF

# 9. Configure Nginx Reverse Proxy
echo "Configuring Nginx reverse proxy..."
sudo tee /etc/nginx/sites-available/aura-relay > /dev/null <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # SSE stream buffering disabled
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
    }
}
EOF

sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/aura-relay /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx

# 10. Enable and start all services
sudo systemctl daemon-reload
sudo systemctl enable aura-relay
sudo systemctl restart aura-relay
sudo systemctl enable aura-git-sync.timer
sudo systemctl start aura-git-sync.timer

echo "========================================================="
echo "   AURA Relay Successfully Deployed and Running!         "
echo "========================================================="
echo "Status check: sudo systemctl status aura-relay"
echo "Live logs:    journalctl -u aura-relay -f"
echo "Git sync:     Continuous automatic sync enabled (every 30s)"
