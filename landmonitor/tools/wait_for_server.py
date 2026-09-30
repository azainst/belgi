"""Wait for a healthy HTTP response before opening the browser."""
import json
import time
import urllib.request
import webbrowser

def wait_and_open(port=8000, timeout=60):
    url = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(url + "/healthz", timeout=1) as response:
                if response.status == 200 and json.load(response).get("ok") is True:
                    webbrowser.open(url)
                    return 0
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    print("Server did not become ready. Check the server error above.")
    return 1

if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    sys.exit(wait_and_open(int(os.getenv("PORT", "8000"))))
