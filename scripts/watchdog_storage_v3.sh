#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
CONFIG="$BASE/config/storage_watchdog.conf"
STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
OUT="$BASE/snapshots/storage/$STAMP"
REPORT="$BASE/reports/watchdog-storage-$STAMP.md"

mkdir -p "$OUT" "$BASE/reports"

if [ -f "$CONFIG" ]; then
  # shellcheck disable=SC1090
  source "$CONFIG"
fi

NAS_PRIMARY_IP="${NAS_PRIMARY_IP:-10.0.0.100}"
NAS_SECONDARY_IP="${NAS_SECONDARY_IP:-10.0.0.6}"

FRIGATE_PRIMARY_DIR="${FRIGATE_PRIMARY_DIR:-/mnt/frigate_nas}"
FRIGATE_SECONDARY_DIR="${FRIGATE_SECONDARY_DIR:-/mnt/frigate_backup}"

HA_BACKUP_PRIMARY_DIR="${HA_BACKUP_PRIMARY_DIR-/mnt/nas1}"
HA_BACKUP_SECONDARY_DIR="${HA_BACKUP_SECONDARY_DIR-/mnt/nas2}"

FRIGATE_FRESH_HOURS="${FRIGATE_FRESH_HOURS:-24}"
HA_BACKUP_FRESH_DAYS="${HA_BACKUP_FRESH_DAYS:-7}"

NAS_WARN_PERCENT="${NAS_WARN_PERCENT:-80}"
NAS_CRIT_PERCENT="${NAS_CRIT_PERCENT:-90}"

ATTENTION="$OUT/attention-needed.txt"
: > "$ATTENTION"

add_attention() {
  echo "- $1" >> "$ATTENTION"
}

hours_old() {
  local file="$1"
  local now
  local mod
  now="$(date +%s)"
  mod="$(stat -c %Y "$file" 2>/dev/null || echo 0)"
  echo $(( (now - mod) / 3600 ))
}

days_old() {
  local file="$1"
  local now
  local mod
  now="$(date +%s)"
  mod="$(stat -c %Y "$file" 2>/dev/null || echo 0)"
  echo $(( (now - mod) / 86400 ))
}

latest_file_under() {
  local dir="$1"
  local pattern="${2:-*}"

  [ -d "$dir" ] || return 1

  find "$dir" -type f -name "$pattern" -printf '%T@|%TY-%Tm-%Td %TH:%TM|%p\n' 2>/dev/null \
    | sort -nr \
    | head -1
}

check_ping() {
  local label="$1"
  local ip="$2"
  local outfile="$OUT/ping-$label.txt"

  echo "$label $ip" > "$outfile"
  if ping -c 2 -W 2 "$ip" >> "$outfile" 2>&1; then
    echo "OK"
  else
    add_attention "$label at $ip did not respond to ping."
    echo "FAIL"
  fi
}

check_mount_and_usage() {
  local label="$1"
  local path="$2"
  local outfile="$OUT/df-$label.txt"

  echo "$label $path" > "$outfile"

  if [ ! -d "$path" ]; then
    add_attention "$label path does not exist: $path"
    echo "missing"
    return
  fi

  if ! mountpoint -q "$path"; then
    add_attention "$label path exists but is not a mountpoint: $path"
    echo "not-mounted" >> "$outfile"
    echo "not-mounted"
    return
  fi

  df -h "$path" >> "$outfile" 2>&1 || true

  usep="$(df -P "$path" | awk 'NR==2 {gsub("%","",$5); print $5}')"

  if [ "${usep:-0}" -ge "$NAS_CRIT_PERCENT" ]; then
    add_attention "$label mount $path is critically full at ${usep}%."
  elif [ "${usep:-0}" -ge "$NAS_WARN_PERCENT" ]; then
    add_attention "$label mount $path is getting full at ${usep}%."
  fi

  echo "mounted ${usep}%"
}

check_frigate_freshness() {
  local label="$1"
  local path="$2"
  local outfile="$OUT/frigate-latest-$label.txt"

  echo "$label $path" > "$outfile"

  latest="$(latest_file_under "$path" "*")"

  if [ -z "${latest:-}" ]; then
    add_attention "$label has no files found under $path."
    echo "none" >> "$outfile"
    return
  fi

  echo "$latest" >> "$outfile"
  latest_path="$(echo "$latest" | cut -d'|' -f3-)"
  age="$(hours_old "$latest_path")"

  echo "Latest file age hours: $age" >> "$outfile"

  if [ "$age" -gt "$FRIGATE_FRESH_HOURS" ]; then
    add_attention "$label latest Frigate/storage file is ${age} hours old. Threshold is ${FRIGATE_FRESH_HOURS} hours. Path: $latest_path"
  fi
}

check_ha_backup_freshness() {
  local label="$1"
  local path="$2"
  local outfile="$OUT/ha-backup-latest-$label.txt"

  echo "$label $path" > "$outfile"

  # Home Assistant backups are usually .tar, but allow compressed variants too.
  latest="$(
    {
      latest_file_under "$path" "*.tar"
      latest_file_under "$path" "*.tar.gz"
      latest_file_under "$path" "*.tgz"
    } 2>/dev/null | sort -nr | head -1
  )"

  if [ -z "${latest:-}" ]; then
    add_attention "$label has no HA backup archive found under $path."
    echo "none" >> "$outfile"
    return
  fi

  echo "$latest" >> "$outfile"
  latest_path="$(echo "$latest" | cut -d'|' -f3-)"
  age="$(days_old "$latest_path")"

  echo "Latest backup age days: $age" >> "$outfile"

  if [ "$age" -gt "$HA_BACKUP_FRESH_DAYS" ]; then
    add_attention "$label latest HA backup is ${age} days old. Threshold is ${HA_BACKUP_FRESH_DAYS} days. Path: $latest_path"
  fi
}

echo "# AI Watchdog Storage/NAS Report v3" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "Host: $(hostname)" >> "$REPORT"
echo "" >> "$REPORT"

echo "Checking NAS reachability..."
primary_ping="$(check_ping primary "$NAS_PRIMARY_IP")"
secondary_ping="$(check_ping secondary "$NAS_SECONDARY_IP")"

echo "Checking mounts and disk usage..."
primary_frigate_mount="$(check_mount_and_usage primary-frigate "$FRIGATE_PRIMARY_DIR")"
secondary_frigate_mount="$(check_mount_and_usage secondary-frigate "$FRIGATE_SECONDARY_DIR")"
if [ -n "${HA_BACKUP_PRIMARY_DIR:-}" ]; then
  primary_backup_mount="$(check_mount_and_usage primary-ha-backup "$HA_BACKUP_PRIMARY_DIR")"
else
  primary_backup_mount="skipped - HA backups handled by HAOS"
fi

if [ -n "${HA_BACKUP_SECONDARY_DIR:-}" ]; then
  secondary_backup_mount="$(check_mount_and_usage secondary-ha-backup "$HA_BACKUP_SECONDARY_DIR")"
else
  secondary_backup_mount="skipped - HA backups handled by HAOS"
fi

echo "Checking Frigate/storage freshness..."
check_frigate_freshness "primary-frigate" "$FRIGATE_PRIMARY_DIR"
check_frigate_freshness "secondary-frigate" "$FRIGATE_SECONDARY_DIR"

echo "Checking HA backup freshness..."
if [ -n "${HA_BACKUP_PRIMARY_DIR:-}" ]; then
  check_ha_backup_freshness "primary-ha-backup" "$HA_BACKUP_PRIMARY_DIR"
else
  echo "primary-ha-backup skipped - HA backups handled by HAOS" > "$OUT/ha-backup-latest-primary-ha-backup.txt"
fi

if [ -n "${HA_BACKUP_SECONDARY_DIR:-}" ]; then
  check_ha_backup_freshness "secondary-ha-backup" "$HA_BACKUP_SECONDARY_DIR"
else
  echo "secondary-ha-backup skipped - HA backups handled by HAOS" > "$OUT/ha-backup-latest-secondary-ha-backup.txt"
fi

echo "## Summary" >> "$REPORT"
echo "" >> "$REPORT"
echo "- NAS primary ${NAS_PRIMARY_IP} ping: ${primary_ping}" >> "$REPORT"
echo "- NAS secondary ${NAS_SECONDARY_IP} ping: ${secondary_ping}" >> "$REPORT"
echo "- Primary Frigate mount ${FRIGATE_PRIMARY_DIR}: ${primary_frigate_mount}" >> "$REPORT"
echo "- Secondary Frigate mount ${FRIGATE_SECONDARY_DIR}: ${secondary_frigate_mount}" >> "$REPORT"
echo "- Primary HA backup mount ${HA_BACKUP_PRIMARY_DIR}: ${primary_backup_mount}" >> "$REPORT"
echo "- Secondary HA backup mount ${HA_BACKUP_SECONDARY_DIR}: ${secondary_backup_mount}" >> "$REPORT"
echo "" >> "$REPORT"

echo "## Disk Usage" >> "$REPORT"
echo '```' >> "$REPORT"
df -h "$FRIGATE_PRIMARY_DIR" "$FRIGATE_SECONDARY_DIR" 2>/dev/null | sort -u >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Latest Frigate/Storage Files" >> "$REPORT"
echo '```' >> "$REPORT"
cat "$OUT"/frigate-latest-*.txt 2>/dev/null >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Latest HA Backups" >> "$REPORT"
echo '```' >> "$REPORT"
cat "$OUT"/ha-backup-latest-*.txt 2>/dev/null >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Attention Needed" >> "$REPORT"
echo "" >> "$REPORT"
if [ -s "$ATTENTION" ]; then
  cat "$ATTENTION" >> "$REPORT"
else
  echo "No storage/NAS attention items found." >> "$REPORT"
fi

echo ""
echo "Done."
echo "Storage snapshot saved to: $OUT"
echo "Storage report saved to:   $REPORT"
