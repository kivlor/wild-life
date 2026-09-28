# wild-life

Raspberry Pi Zero wildlife camera: heartbeat + PIR/ultrasonic-triggered capture,
uploads to dynames over scp. Designed to be battery-friendly (small JPEGs,
short WiFi bursts, buffers locally if the network drops).

## Setup (on the Pi)

```bash
git clone <this repo> ~/wild-life && cd ~/wild-life
chmod +x setup.sh
./setup.sh
```

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
- SSH key auth to dynames should be set up (`ssh-copy-id user@dynames`) so scp
  works unattended (script uses `BatchMode=yes`).