# wild-life

Raspberry Pi Zero wildlife camera: heartbeat + PIR/ultrasonic-triggered capture,
uploads to a server of your choosing over scp. Designed to be battery-friendly (small JPEGs,
short WiFi bursts, buffers locally if the network drops).

## Setup (on the Pi)

```bash
curl -fsSL https://raw.githubusercontent.com/kivlor/wild-life/refs/heads/main/setup.sh | bash
```

The script bootstraps itself: if the repo isn't already on disk it installs git,
clones itself into `~/wild-life` (override with `INSTALL_DIR=...`), and re-runs
from inside the clone.

The setup script will:
- install system packages (libcamera-apps, python3-venv, etc.)
- create a `.venv` with Python deps
- generate `config.yaml` from defaults (edit it!)
- install a systemd service (`wildlife.service`, enabled but not started)

## Configure

```bash
nano config.yaml   # set upload host/user, enable PIR/ultrasonic when wired
```

## Run

```bash
# one-shot test (capture + upload + exit)
source .venv/bin/activate
python wildlife.py --once

# as a service
sudo systemctl start wildlife
journalctl -u wildlife -f
```

## Hardware notes

- **PIR (HC-SR501 / Jaycar XC4444)**: OUT is 3.3V — wire direct to GPIO. Give it
  ~60s warm-up (script handles this). Avoid aiming at sunlit foliage.
- **HC-SR04 ultrasonic**: ECHO pin is 5V — **must** go through a voltage divider
  (e.g. 1k/2k resistors) before the GPIO pin. Aim at the feeder as a tripwire.
- SSH key auth to the upload host should be set up (`ssh-copy-id user@your-host`) so scp
  works unattended (script uses `BatchMode=yes`).