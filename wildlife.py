#!/usr/bin/env python3
"""Wildlife camera: heartbeat + PIR/ultrasonic-triggered capture, scp upload."""

import argparse
import logging
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

log = logging.getLogger("wildlife")

RUNNING = True


def _stop(signum, frame):
    global RUNNING
    RUNNING = False


class Capture:
    """Camera capture via libcamera-still (works on Zero / any Pi OS with libcamera)."""

    def __init__(self, cfg: dict, out_dir: Path):
        self.cfg = cfg
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def snap(self, prefix: str = "img") -> Path | None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.out_dir / f"{prefix}-{ts}.jpg"
        cmd = [
            "libcamera-still", "-o", str(path),
            "--width", str(self.cfg.get("width", 800)),
            "--height", str(self.cfg.get("height", 600)),
            "-q", str(self.cfg.get("quality", 50)),
            "--nopreview", "-t", "1000",
        ]
        try:
            subprocess.run(cmd, check=True, timeout=20,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return path
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            log.error("capture failed: %s", e)
            return None


class Uploader:
    def __init__(self, cfg: dict):
        self.host = cfg["host"]
        self.user = cfg.get("user")
        self.remote_dir = cfg.get("remote_dir", "~/wildlife")
        dest = f"{self.user}@{self.host}" if self.user else self.host

    def upload(self, path: Path, keep_local: bool = True) -> bool:
        cmd = ["scp", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
               str(path), f"{self.dest}:{self.remote_dir}/"]
        for attempt in range(3):
            try:
                subprocess.run(cmd, check=True, timeout=60)
                if not keep_local:
                    path.unlink(missing_ok=True)
                log.info("uploaded %s", path.name)
                return True
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                log.warning("upload failed (attempt %d): %s", attempt + 1, e)
                time.sleep(10 * (attempt + 1))
        log.error("giving up on %s; kept locally", path.name)
        return False

    @property
    def dest(self):
        return f"{self.user}@{self.host}" if self.user else self.host


class PIR:
    """HC-SR501-style PIR on a GPIO pin (3.3V output, Pi-safe)."""

    def __init__(self, pin: int):
        import RPi.GPIO as GPIO
        self.GPIO = GPIO
        self.pin = pin
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    def triggered(self) -> bool:
        return self.GPIO.input(self.pin) == self.GPIO.HIGH

    def cleanup(self):
        self.GPIO.cleanup(self.pin)


class Ultrasonic:
    """HC-SR04 distance trigger. ECHO must go through a voltage divider (5V -> 3.3V)."""

    def __init__(self, trig_pin: int, echo_pin: int, threshold_cm: float | None = None):
        import RPi.GPIO as GPIO
        self.GPIO = GPIO
        self.trig, self.echo = trig_pin, echo_pin
        self.threshold = threshold_cm
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(trig_pin, GPIO.OUT)
        GPIO.setup(echo_pin, GPIO.IN)
        self.baseline = None

    def distance_cm(self) -> float | None:
        g = self.GPIO
        g.output(self.trig, False); time.sleep(0.00005)
        g.output(self.trig, True); time.sleep(0.00001)
        g.output(self.trig, False)
        pulse_start = None
        t0 = time.time()
        while g.input(self.echo) == 0:
            if time.time() - t0 > 0.03: return None
            pulse_start = time.time()
        if pulse_start is None: return None
        while g.input(self.echo) == 1:
            if time.time() - pulse_start > 0.03: return None
        return (time.time() - pulse_start) * 17150

    def calibrate(self, samples: int = 20):
        reads = [d for d in (self.distance_cm() for _ in range(samples)) if d]
        if reads:
            self.baseline = sorted(reads)[len(reads) // 2]
            log.info("ultrasonic baseline: %.0f cm", self.baseline)
        else:
            log.error("ultrasonic calibration failed — check wiring")

    def triggered(self) -> bool:
        d = self.distance_cm()
        if d is None: return False
        if self.baseline is None:
            self.baseline = d
            return False
        return abs(d - self.baseline) > (self.threshold or 15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="capture one image, upload, exit")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        sys.exit(f"config not found: {cfg_path} (copy config.example.yaml)")
    cfg = yaml.safe_load(cfg_path.read_text())

    cap_cfg = cfg.get("capture", {})
    log_cfg = cfg.get("logging", {})
    cap = Capture(cap_cfg, Path(log_cfg.get("local_dir", "captures")))
    up = Uploader(cfg.get("upload", {"host": "upload.example.internal"}))

    pir = ultrasonic = None
    if cfg.get("pir", {}).get("enabled"):
        pir = PIR(cfg["pir"]["pin"])
        log.info("PIR on GPIO%d (warmup %ds)",
                 cfg["pir"]["pin"], cfg["pir"].get("warmup", 60))
    if cfg.get("ultrasonic", {}).get("enabled"):
        uc = cfg["ultrasonic"]
        ultrasonic = Ultrasonic(uc["trig_pin"], uc["echo_pin"])
        ultrasonic.calibrate()
        log.info("ultrasonic on trig=%d echo=%d", uc["trig_pin"], uc["echo_pin"])

    if args.once:
        p = cap.snap("test")
        if p: up.upload(p)
        return

    boot = time.time()
    last_pir = 0.0
    last_uss = 0.0
    last_beat = 0.0
    interval = cap_cfg.get("interval", 60)
    p_warmup = cfg.get("pir", {}).get("warmup", 60)
    p_debounce = cfg.get("pir", {}).get("debounce", 30)
    u_debounce = cfg.get("ultrasonic", {}).get("debounce", 30)

    log.info("running: heartbeat every %ds, pir=%s, ultrasonic=%s",
             interval, bool(pir), bool(ultrasonic))

    while RUNNING:
        now = time.time()

        if pir and now - boot > p_warmup and pir.triggered():
            if now - last_pir > p_debounce:
                log.info("PIR trigger")
                p = cap.snap("pir")
                if p: up.upload(p)
                last_pir = now

        if ultrasonic and ultrasonic.triggered():
            if now - last_uss > u_debounce:
                log.info("ultrasonic trigger")
                p = cap.snap("us")
                if p: up.upload(p)
                last_uss = now

        if now - last_beat >= interval:
            p = cap.snap("beat")
            if p: up.upload(p)
            last_beat = now

        time.sleep(0.3)

    log.info("shutting down")
    if pir: pir.cleanup()


if __name__ == "__main__":
    main()