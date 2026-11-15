"""Локальный веб-сервер comic-comfy: 127.0.0.1 и браузер.

Запускается из любого каталога: корень репозитория считается от этого файла,
а не от текущего каталога.

    python webapp/run.py
    python webapp/run.py --port 8090 --no-browser
"""
import argparse
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def port_busy(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="comic-comfy: локальный веб-интерфейс")
    ap.add_argument("--host", default="127.0.0.1", help="адрес; по умолчанию только loopback")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    a = ap.parse_args(argv)
    if port_busy(a.host, a.port):
        print(f"порт {a.port} занят — укажи другой: --port 8090", file=sys.stderr)
        return 1
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"внимание: сервер слушает {a.host}, а не только loopback", file=sys.stderr)
    from webapp.ui import start
    print(f"comic-comfy: http://{a.host}:{a.port}")
    start(host=a.host, port=a.port, show=not a.no_browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
