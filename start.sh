#!/bin/bash

# Configuration variables
mt5file='/config/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe'
WINEPREFIX='/config/.wine'
WINEDEBUG='-all'
wine_executable="wine"
metatrader_version="5.0.36"
mt5server_port="8001"
MT5_CMD_OPTIONS="${MT5_CMD_OPTIONS:-}"
mono_url="https://dl.winehq.org/wine/wine-mono/10.3.0/wine-mono-10.3.0-x86.msi"
python_url="https://www.python.org/ftp/python/3.9.13/python-3.9.13.exe"
mt5setup_url="https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"

show_message() {
    echo "$1"
}

check_dependency() {
    if ! command -v "$1" &> /dev/null; then
        echo "$1 is not installed."
        exit 1
    fi
}

check_dependency "curl"
check_dependency "$wine_executable"

# Install Mono if not present
if [ ! -e "/config/.wine/drive_c/windows/mono" ]; then
    show_message "[1/7] Downloading and installing Mono..."
    curl -o /config/.wine/drive_c/mono.msi "$mono_url"
    WINEDLLOVERRIDES=mscoree=d $wine_executable msiexec /i /config/.wine/drive_c/mono.msi /qn
    rm -f /config/.wine/drive_c/mono.msi
    show_message "[1/7] Mono installed."
fi

# Check if MetaTrader 5 is already installed
if [ -e "$mt5file" ]; then
    show_message "[2/7] File $mt5file already exists."
else
    show_message "[2/7] Installing MetaTrader 5..."
    $wine_executable reg add "HKEY_CURRENT_USER\\Software\\Wine" /v Version /t REG_SZ /d "win10" /f
    curl -o /config/.wine/drive_c/mt5setup.exe "$mt5setup_url"
    $wine_executable "/config/.wine/drive_c/mt5setup.exe" "/auto" &
    wait
    rm -f /config/.wine/drive_c/mt5setup.exe
fi

# Run MT5
if [ -e "$mt5file" ]; then
    show_message "[4/7] Running MT5..."
    $wine_executable "$mt5file" $MT5_CMD_OPTIONS &
fi

# Install Python in Wine if not present
if ! $wine_executable python --version 2>/dev/null; then
    show_message "[5/7] Installing Python in Wine..."
    curl -L "$python_url" -o /tmp/python-installer.exe
    $wine_executable /tmp/python-installer.exe /quiet InstallAllUsers=1 PrependPath=1
    rm -f /tmp/python-installer.exe
fi

# Ensure correct packages in Wine Python (NumPy 1.x, rpyc 5.3.1, MetaTrader5)
if ! $wine_executable python -c "import MetaTrader5, rpyc" 2>/dev/null; then
    show_message "[6/7] Installing Wine Python dependencies (MetaTrader5, rpyc, numpy)..."
    $wine_executable python -m pip install --no-cache-dir "numpy<2" "rpyc==5.3.1" "MetaTrader5"
else
    show_message "[6/7] Wine Python dependencies already installed."
fi

# Kill any stale server.py instances
pkill -f server.py 2>/dev/null || true
sleep 1

# Create / overwrite server.py with reuse_addr=True and proper logging
cat << 'PYEOF' > /config/server.py
import rpyc
from rpyc.utils.server import ThreadedServer
import time
import sys

print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] >>> RPYC SUNUCUSU BASLATILDI (0.0.0.0:8001) <<<", flush=True)
try:
    server = ThreadedServer(
        rpyc.SlaveService,
        hostname="0.0.0.0",
        port=8001,
        reuse_addr=True,
        protocol_config={"allow_all_attrs": True}
    )
    server.start()
except Exception as e:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Server error: {e}", file=sys.stderr, flush=True)
    sys.exit(1)
PYEOF

# Create daemon runner loop to ensure RPyC is always alive
cat << 'RUNEOF' > /config/run_server.sh
#!/bin/bash
while true; do
    echo "[$(date)] Starting MT5 RPyC bridge server..." >> /config/server.log
    wine python /config/server.py >> /config/server.log 2>&1
    echo "[$(date)] server.py exited with code $?, restarting in 2s..." >> /config/server.log
    sleep 2
done
RUNEOF
chmod +x /config/run_server.sh

# Start the RPyC server supervisor inside Wine
show_message "[7/7] Starting the MT5 RPyC bridge supervisor on port $mt5server_port..."
nohup /config/run_server.sh > /dev/null 2>&1 &

sleep 3
if ss -tuln | grep ":$mt5server_port" > /dev/null; then
    show_message "[7/7] The MT5 bridge server is RUNNING on port $mt5server_port."
else
    show_message "[7/7] Waiting for MT5 bridge server to bind to port $mt5server_port..."
fi

