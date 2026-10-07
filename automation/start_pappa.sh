#!/bin/bash
# Starts the dashboard (run.py) and the public ngrok tunnel, if not already running.
# Triggered daily at 08:45 IST by a Windows Task Scheduler task.

PROJECT_DIR="/home/poojithajsiri/projects/Pappa_live_data_collecting"
PYTHON="/home/poojithajsiri/miniconda3/bin/python3"
NGROK="/home/poojithajsiri/.local/bin/ngrok"
NGROK_URL="https://blubber-curing-phonics.ngrok-free.dev"
LOG_DIR="$PROJECT_DIR/automation/logs"
TODAY=$(date +%F)

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR" || exit 1

if ! pgrep -f "python3 run.py" > /dev/null; then
    nohup "$PYTHON" run.py > "$LOG_DIR/run_$TODAY.log" 2>&1 &
    disown
    echo "$(date '+%H:%M:%S') started run.py (pid $!)" >> "$LOG_DIR/automation.log"
else
    echo "$(date '+%H:%M:%S') run.py already running" >> "$LOG_DIR/automation.log"
fi

sleep 4   # let the dashboard bind to :8000 before the tunnel tries to reach it

if ! pgrep -f "ngrok http 8000" > /dev/null; then
    nohup "$NGROK" http 8000 --url "$NGROK_URL" > "$LOG_DIR/ngrok_$TODAY.log" 2>&1 &
    disown
    echo "$(date '+%H:%M:%S') started ngrok (pid $!)" >> "$LOG_DIR/automation.log"
else
    echo "$(date '+%H:%M:%S') ngrok already running" >> "$LOG_DIR/automation.log"
fi
