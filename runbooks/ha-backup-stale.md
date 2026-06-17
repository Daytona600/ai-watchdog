# HA Backup Stale

Home Assistant backup sensors show the last successful automatic backup is too old, unknown, or unavailable.

Check HA backup sensors:

source ~/ai-watchdog/config/ha_token.env

for ent in \
  sensor.backup_backup_manager_state \
  sensor.backup_last_successful_automatic_backup \
  sensor.backup_last_attempted_automatic_backup \
  sensor.backup_next_scheduled_automatic_backup
do
  echo
  echo "=== $ent ==="
  curl -s \
    -H "Authorization: Bearer $HA_TOKEN" \
    -H "Content-Type: application/json" \
    "$HA_BASE_URL/api/states/$ent" \
  | python3 -m json.tool
done

Also check Home Assistant UI:

Settings -> System -> Backups
