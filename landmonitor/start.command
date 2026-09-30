#!/bin/bash
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
python3 tools/launch.py
result=$?
read -n 1 -s -r -p "Press any key to close..."
echo
exit "$result"
