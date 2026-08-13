#!/usr/bin/env python3
"""Start the Edvid preview as a detached, UTF-8-safe local process."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
SERVER = HERE / "preview_server.py"


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start the Edvid preview server in the background"
    )
    parser.add_argument("--root", type=Path, required=True, help="session edit dir")
    parser.add_argument("--port", type=int, default=4820)
    args = parser.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"edit dir not found: {root}")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not SERVER.is_file():
        parser.error(f"preview server not found: {SERVER}")
    if not port_available(args.port):
        parser.error(f"port {args.port} is already in use")

    stdout_path = root / "preview-server.log"
    stderr_path = root / "preview-server.err.log"
    command = [
        sys.executable,
        "-X",
        "utf8",
        str(SERVER),
        "--root",
        str(root),
        "--port",
        str(args.port),
    ]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"

    creationflags = 0
    start_new_session = os.name != "nt"
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            command,
            cwd=HERE,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )

    print(
        f"Edvid preview started (PID {process.pid}) -> "
        f"http://127.0.0.1:{args.port}"
    )
    print(f"logs: {stdout_path} | {stderr_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
