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
# Корень репозитория в sys.path добавляет tools\settings.py — единственное место,
# где это делается. Здесь нужен только tools: из него импортируется settings.
_TOOLS = ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


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
    # Задание печатает по-русски (логи теггера, этапы). В пайп или файл Python пишет
    # в кодировке локали, и первая такая строка роняла бы всё задание
    # UnicodeEncodeError — поэтому поток переводим в UTF-8 до старта сервера.
    import settings
    settings.utf8_streams()
    from webapp.ui import start
    print(f"comic-comfy: http://{a.host}:{a.port}")
    try:
        start(host=a.host, port=a.port, show=not a.no_browser)
    except KeyboardInterrupt:
        # Ctrl+C — обычная остановка, а не авария: без трейсбека.
        print("\ncomic-comfy: остановлено")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
