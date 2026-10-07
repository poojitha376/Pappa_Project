#!/bin/bash
# Stops run.py and the ngrok tunnel.
# Triggered daily at 15:45 IST by a Windows Task Scheduler task (5 min after the
# last scheduled capture at 15:40, so the final row finishes writing first).

LOG_DIR="/home/poojithajsiri/projects/Pappa_live_data_collecting/automation/logs"
mkdir -p "$LOG_DIR"

pkill -f "python3 run.py" 2>/dev/null && echo "$(date '+%H:%M:%S') stopped run.py" >> "$LOG_DIR/automation.log"
pkill -f "ngrok http 8000" 2>/dev/null && echo "$(date '+%H:%M:%S') stopped ngrok" >> "$LOG_DIR/automation.log"
