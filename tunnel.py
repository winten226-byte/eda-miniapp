# -*- coding: utf-8 -*-
"""
Запуск сервера Mini App + публичная HTTPS-ссылка через localhost.run.

Запуск:
    python tunnel.py
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

import app

PORT = 8000
URL_RE = re.compile(r"https://[a-z0-9][a-z0-9.-]*\.lhr\.life", re.I)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    parser = argparse.ArgumentParser(description="Mini App + localhost.run")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    ssh = shutil.which("ssh")
    if not ssh:
        print("Не найден ssh. Установите OpenSSH Client.", flush=True)
        sys.exit(1)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Локальный сервер: http://localhost:{args.port}", flush=True)
    print("Подключаем публичный туннель localhost.run...", flush=True)

    known_hosts = os.path.join(HERE, ".ssh_known_hosts")
    cmd = [
        ssh,
        "-T",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-R",
        f"80:127.0.0.1:{args.port}",
        "nokey@localhost.run",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        errors="replace",
    )

    url_file = os.path.join(HERE, "tunnel_url.txt")
    announced = False
    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = ANSI_RE.sub("", raw_line).rstrip()
            if "\x1b[7m" in raw_line or not line.strip():
                continue
            match = URL_RE.search(line)
            if match and not announced:
                url = match.group(0)
                announced = True
                print("\n" + "=" * 74, flush=True)
                print(f"ПУБЛИЧНАЯ HTTPS-ССЫЛКА: {url}", flush=True)
                print("=" * 74 + "\n", flush=True)
                try:
                    with open(url_file, "w", encoding="utf-8") as fh:
                        fh.write(url + "\n")
                    print(f"Ссылка сохранена в {url_file}", flush=True)
                except OSError:
                    pass
            else:
                print(line, flush=True)
    except KeyboardInterrupt:
        print("\nОстановлено.", flush=True)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()