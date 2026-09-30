"""Run the web panel and optional Telegram bot."""
import os
import sys
import threading
import app  # Load .env before reading PORT or importing services.
import uvicorn

if __name__ == "__main__":
    try:
        port = int(os.getenv("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        sys.exit("PORT must be an integer between 1 and 65535.")
    if "--open-browser" in sys.argv:
        from tools.wait_for_server import wait_and_open
        threading.Thread(target=wait_and_open, args=(port,), daemon=True).start()
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, timeout_graceful_shutdown=3)
