#!/bin/bash
# Container entry point. Starts: virtual display -> MT5 bridge (Windows Python under Wine, next to the
# MetaTrader 5 terminal) -> Node dashboard/server on $PORT. Missing Wine components are retried in the background.
set -u
cd "$(dirname "$0")"
export DISPLAY="${DISPLAY:-:99}" WINEDEBUG=-all
export WINEPREFIX="${WINEPREFIX:-$HOME/.wine}"
export PORT="${PORT:-3000}"
MT5="$WINEPREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe"

Xvfb "$DISPLAY" -screen 0 1024x768x16 >/dev/null 2>&1 &
sleep 2

(
  # Background: install whatever the image build could not finish, then keep the bridge running forever.
  if ! wine python -V >/dev/null 2>&1; then
    wget -q -O /tmp/py.exe https://www.python.org/ftp/python/3.9.13/python-3.9.13-amd64.exe && wine /tmp/py.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
  fi
  wine python -c "import MetaTrader5" >/dev/null 2>&1 || wine python -m pip install --no-cache-dir MetaTrader5
  if [ ! -f "$MT5" ]; then
    wget -q -O /tmp/mt5setup.exe https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
    timeout 300 wine /tmp/mt5setup.exe /auto || true
  fi
  while true; do
    echo "[start] launching MT5 bridge"
    wine python mt5_bridge.py
    echo "[start] bridge stopped, restarting in 5s"; sleep 5
  done
) &

exec node server.js
