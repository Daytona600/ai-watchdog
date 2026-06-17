# Watchdog Timer Stopped

The hourly heartbeat detected that the daily watchdog timer is stopped, disabled, or the report is too old.

Check timers:

systemctl --user list-timers --all | grep ai-watchdog
systemctl --user status ai-watchdog.timer --no-pager
systemctl --user status ai-watchdog-heartbeat.timer --no-pager

Restart timers:

systemctl --user daemon-reload
systemctl --user enable --now ai-watchdog.timer
systemctl --user enable --now ai-watchdog-heartbeat.timer

Run manually:

~/ai-watchdog/scripts/watchdog_master_v2.sh
~/ai-watchdog/scripts/watchdog_heartbeat_v1.sh
cat ~/ai-watchdog/public/watchdog-heartbeat.json
