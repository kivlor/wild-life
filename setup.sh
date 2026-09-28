#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# ----------------------------
# Config (override via env)
# ----------------------------
DEVICE_NAME="${DEVICE_NAME:-wildlife}"
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="$(id -un)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# ----------------------------
# Bootstrap: when piped in via `curl | bash` the repo isn't on disk yet.
# Clone it and re-run setup.sh from inside the clone.
# ----------------------------
if [ ! -f wildlife.py ]; then
  # Piped in via `curl | bash` (or run outside the repo) — clone and re-run.
  DEST="${INSTALL_DIR:-$HOME/wild-life}"
  echo "==> Repo files not found in $(pwd) — cloning wild-life to $DEST"
  if ! command -v git >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y git
  fi
  git clone https://github.com/kivlor/wild-life.git "$DEST"
  cd "$DEST"
  exec bash setup.sh
fi

# ----------------------------
# Sanity checks
# ----------------------------
echo "==> Checking environment"
if ! grep -qi raspbian /etc/os-release 2>/dev/null && [ ! -f /boot/firmware/config.txt ] && [ ! -f /boot/config.txt ]; then
  echo "WARNING: This doesn't look like a Raspberry Pi OS system. Continuing anyway..."
fi

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  echo "ERROR: $PYTHON_BIN not found"; exit 1; }

if [ ! -d /proc/device-tree ] && [ ! -f /boot/firmware/config.txt ] && [ ! -f /boot/config.txt ]; then
  echo "ERROR: Not running on a Raspberry Pi"
  exit 1
fi

# ----------------------------
# System updates & core
# ----------------------------
echo "==> Updating system"
sudo apt-get update -y

echo "==> Installing core packages"
sudo apt-get install -y \
  git \
  curl \
  wget \
  htop \
  tmux \
  jq \
  libcamera-apps \
  python3 \
  python3-venv \
  python3-picamera2 \
  python3-libcamera \
  python3-pil \
  rpi.GPIO || {
    # Bookworm can be fussy about python3-picamera2 in apt under venv setups;
    # fall back to the basics and install python deps via pip later.
    sudo apt-get install -y git curl wget htop tmux jq libcamera-apps python3 python3-venv
  }

# ----------------------------
# Camera check
# ----------------------------
echo "==> Checking camera"
if command -v libcamera-hello >/dev/null 2>&1; then
  if sudo libcamera-hello --list-cameras 2>/dev/null | grep -q "Available cameras"; then
    echo "    Camera detected."
  else
    echo "WARNING: No camera detected. Check the ribbon cable and enable the camera:"
    echo "    sudo raspi-config -> Interface Options -> Camera"
  fi
else
  echo "WARNING: libcamera-apps not found; camera capture will fail."
fi

# ----------------------------
# Python environment
# ----------------------------
echo "==> Creating virtual environment"
VENV_DIR="$INSTALL_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv --system-site-packages "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "==> Installing Python dependencies"
pip install --upgrade pip
pip install -r "$INSTALL_DIR/requirements.txt"

# ----------------------------
# Config
# ----------------------------
echo "==> Setting up config"
if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
  cat > "$INSTALL_DIR/config.yaml" <<EOF
# Wildlife cam configuration
# See config.example.yaml for all options
upload:
  host: upload.example.internal   # change to your upload server
  user: "${SERVICE_USER}"
  remote_dir: ~/wildlife
capture:
  width: 800
  height: 600
  quality: 50
  interval: 60          # seconds between heartbeat captures
pir:
  enabled: false       # set true once a PIR sensor is wired to GPIO
  pin: 17
  warmup: 60            # seconds to ignore triggers after boot
  debounce: 30          # seconds between PIR-triggered captures
ultrasonic:
  enabled: false        # set true once HC-SR04 is wired (needs 5V + divider!)
  trig_pin: 23
  echo_pin: 24
  debounce: 30
logging:
  local_dir: "$INSTALL_DIR/captures"
EOF
  echo "    Created config.yaml — edit host/capture settings before running."
else
  echo "    config.yaml already exists, leaving it alone."
fi

mkdir -p "$INSTALL_DIR/captures"

# ----------------------------
# Systemd service
# ----------------------------
echo "==> Installing systemd service"
sudo tee /etc/systemd/system/wildlife.service >/dev/null <<EOF
[Unit]
Description=Wildlife camera capture + upload
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python $INSTALL_DIR/wildlife.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable wildlife.service

echo ""
echo "==> Done. Next steps:"
echo "    1. Edit config.yaml (upload host, PIR/ultrasonic pins)"
echo "    2. Test manually:  source .venv/bin/activate && python wildlife.py --once"
echo "    3. Start the service:  sudo systemctl start wildlife"
echo "    4. Watch logs:  journalctl -u wildlife -f"