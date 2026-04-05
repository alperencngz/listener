#!/bin/bash
# One-shot check: restart listener automate if not running
# Scheduled for 2026-04-05 00:15 Istanbul time

LOG="/Users/alperencngzz/Desktop/listener/automation_logs/cron_check.log"
echo "[$(date)] Cron check triggered" >> "$LOG"

# Check if listener automate is already running
if pgrep -f "listener automate" > /dev/null 2>&1; then
    echo "[$(date)] Pipeline is already running. Nothing to do." >> "$LOG"
else
    echo "[$(date)] Pipeline not running. Starting..." >> "$LOG"
    cd /Users/alperencngzz/Desktop/listener
    nohup bash -c 'until /opt/anaconda3/bin/listener automate; do echo ">>> Retry at $(date)"; sleep 300; done' >> "$LOG" 2>&1 &
    echo "[$(date)] Pipeline started (PID: $!)" >> "$LOG"
fi

# Remove self from crontab (one-shot)
crontab -l 2>/dev/null | grep -v "check_and_restart.sh" | crontab -
echo "[$(date)] Cron entry removed (one-shot done)" >> "$LOG"
