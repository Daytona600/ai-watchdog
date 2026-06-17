# Back Door Lock Unavailable

Home Assistant cannot read or control lock.back_door_lock.

First checks:

- Replace/check batteries.
- Test lock/unlock with the door open.
- If it sounds strained, check deadbolt and strike plate alignment.
- If fresh batteries do not help, check the wireless mesh/radio route.

Check from main server:

source ~/ai-watchdog/config/ha_token.env

curl -s \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  "$HA_BASE_URL/api/states/lock.back_door_lock" \
| python3 -m json.tool

Clear alert after fixing:

~/ai-watchdog/scripts/watchdog_master_v2.sh
~/ai-watchdog/scripts/watchdog_alert_if_needed.sh
cat ~/ai-watchdog/public/alert.txt
