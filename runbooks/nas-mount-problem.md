# NAS Mount Problem

A NAS path is missing, unreachable, stale, or too full.

Check reachability:

ping -c 2 10.0.0.100
ping -c 2 10.0.0.6

Check mounts:

findmnt | grep -E '/mnt/frigate_nas|/mnt/frigate_backup|/mnt/nas1|/mnt/nas2|/mnt/nas/public' || true
df -h | grep -E '/mnt/frigate_nas|/mnt/frigate_backup|/mnt/nas1|/mnt/nas2|/mnt/nas/public' || true

Rerun storage watchdog:

~/ai-watchdog/scripts/watchdog_storage_v3.sh
grep -A40 "## Attention Needed" "$(ls -t ~/ai-watchdog/reports/watchdog-storage-*.md | head -1)"
