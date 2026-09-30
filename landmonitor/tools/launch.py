"""Create a platform-local environment and launch the application."""
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent

def main():
    os.chdir(ROOT)
    env = ROOT / ".venv"
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    try:
        valid = subprocess.run([str(python), "-c", "import sys; assert sys.version_info >= (3, 10)"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        valid = False
    if not valid:
        if env.exists():
            backup = ROOT / (".venv.backup-" + str(time.time_ns()))
            env.rename(backup)
            print("Previous environment saved:", backup, flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "-r", "requirements.txt"], check=True)
    return subprocess.call([str(python), "run.py", "--open-browser"])

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError) as exc:
        print("Startup failed:", exc, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)
