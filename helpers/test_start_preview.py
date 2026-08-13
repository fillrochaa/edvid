#!/usr/bin/env python3
"""Smoke-test the detached preview launcher with a non-ASCII edit path."""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


HERE = Path(__file__).resolve().parent
STARTER = HERE / "start_preview.py"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def stop_process(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    else:
        os.kill(pid, signal.SIGTERM)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.05)


def main() -> int:
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="edvid-preview-") as tmp:
        edit = Path(tmp) / "edição com espaços"
        edit.mkdir()
        (edit / "state.json").write_text(
            json.dumps({"project": "Windows smoke test", "phase": 1}),
            encoding="utf-8",
        )
        started = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(STARTER),
                "--root",
                str(edit),
                "--port",
                str(port),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        match = re.search(r"PID (\d+)", started.stdout)
        if not match:
            raise RuntimeError(f"unexpected launcher output: {started.stdout!r}")
        pid = int(match.group(1))

        try:
            endpoint = f"http://127.0.0.1:{port}/api/state"
            deadline = time.monotonic() + 10
            while True:
                try:
                    with urllib.request.urlopen(endpoint, timeout=1) as response:
                        payload = json.load(response)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("preview server did not become ready")
                    time.sleep(0.1)

            if payload.get("state", {}).get("project") != "Windows smoke test":
                raise RuntimeError(f"unexpected state response: {payload!r}")
        finally:
            stop_process(pid)

        print(f"preview smoke test passed on port {port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
