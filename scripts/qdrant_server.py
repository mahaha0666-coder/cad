#!/usr/bin/env python3
"""Start, stop, and inspect the project-local Qdrant Server process."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "services" / "qdrant" / "bin" / "qdrant"
CONFIG = ROOT / "services" / "qdrant" / "config.yaml"
RUNTIME = ROOT / ".data" / "qdrant-server"
PID_FILE = RUNTIME / "qdrant.pid"
LOG_FILE = RUNTIME / "logs" / "qdrant.log"
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 6333


def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def ready() -> bool:
    try:
        with socket.create_connection((HTTP_HOST, HTTP_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def tail_log(lines: int = 20) -> str:
    if not LOG_FILE.exists():
        return ""
    return "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def start() -> int:
    pid = read_pid()
    if process_alive(pid):
        print(json.dumps({"status": "already_running", "pid": pid, "ready": ready()}))
        return 0
    if not BINARY.is_file() or not os.access(BINARY, os.X_OK):
        raise SystemExit(f"Qdrant binary is missing or not executable: {BINARY}")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "storage").mkdir(exist_ok=True)
    (RUNTIME / "snapshots").mkdir(exist_ok=True)
    (RUNTIME / "logs").mkdir(exist_ok=True)
    with LOG_FILE.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            [str(BINARY), "--config-path", str(CONFIG)],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    PID_FILE.write_text(str(process.pid) + "\n", encoding="utf-8")
    for _ in range(40):
        if ready():
            print(json.dumps({"status": "running", "pid": process.pid, "ready": True}))
            return 0
        if process.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            raise SystemExit(f"Qdrant exited with code {process.returncode}\n{tail_log()}")
        time.sleep(0.25)
    raise SystemExit(f"Qdrant did not become ready within 10 seconds\n{tail_log()}")


def stop() -> int:
    pid = read_pid()
    if not process_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        print(json.dumps({"status": "not_running"}))
        return 0
    assert pid is not None
    os.kill(pid, signal.SIGTERM)
    for _ in range(40):
        if not process_alive(pid):
            PID_FILE.unlink(missing_ok=True)
            print(json.dumps({"status": "stopped", "pid": pid}))
            return 0
        time.sleep(0.25)
    os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    print(json.dumps({"status": "killed", "pid": pid}))
    return 0


def status() -> int:
    pid = read_pid()
    alive = process_alive(pid)
    result = {
        "status": "running" if alive else "stopped",
        "pid": pid,
        "ready": ready() if alive else False,
        "http_url": "http://127.0.0.1:6333",
        "grpc_address": "127.0.0.1:6334",
        "log_file": str(LOG_FILE),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if alive and result["ready"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "restart", "status"])
    args = parser.parse_args()
    if args.action == "start":
        return start()
    if args.action == "stop":
        return stop()
    if args.action == "restart":
        stop()
        return start()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
