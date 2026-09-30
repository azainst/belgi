"""Ждёт, пока сервер по-настоящему ответит на /healthz, прежде чем открывать браузер.

Раньше стартовые скрипты просто ждали фиксированные 2 секунды и открывали localhost —
если компьютер был медленнее обычного или "localhost" указывал не на тот адрес (IPv6
вместо IPv4), браузер писал "сервер недоступен". Эта проверка устраняет обе причины:
ждёт реальной готовности и всегда использует 127.0.0.1.
"""
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8000/healthz"
ATTEMPTS = 60  # секунд


def main() -> int:
    for _ in range(ATTEMPTS):
        try:
            urllib.request.urlopen(URL, timeout=1)
            return 0
        except Exception:
            time.sleep(1)
    return 1


if __name__ == "__main__":
    sys.exit(main())
