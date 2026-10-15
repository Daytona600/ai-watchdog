# Netdata Alert

Netdata (added 2026-10-14) runs on all seven Linux machines. Each one streams its metrics every second to a parent on the EPYC box (10.0.0.188), which keeps the history (14 days per-second, 3 months per-minute, 2 years per-hour) and evaluates the alerts. Netdata's own email/push notifications are off: the watchdog reads the parent's API instead, so everything still arrives through this dashboard and HA.

Open the dashboard from any machine on the LAN: http://10.0.0.188:19999 (pick a node in the left-hand list). Each node's chart menu has Disks, Hardware (IPMI, EDAC, PCIe), Systemd services and Docker sections.

Two dashboard rows come from Netdata:

- **Netdata Nodes**: a machine stopped streaming. It has hung, rebooted, lost power or dropped off the network. If several drop at once, suspect power (see the every-other-Thursday blip) or the switch. If only the EPYC parent is unreachable, the whole check fails: `ssh apex-agent-second 'systemctl status netdata'`.
- **Netdata Alerts**: an alert is raised. The detail names the machine, what, the value and the alert's internal name.

## What the common alerts mean

| Alert name | Meaning | What to do |
|---|---|---|
| home_smart_reallocated_sectors / home_smart_pending_sectors / home_smart_offline_uncorrectable | A drive is remapping or failing to read sectors | One-off small numbers: watch for growth. Rising: plan a replacement. On the Z4 RAID5, replace before a second drive goes. `sudo smartctl -a /dev/sdX` |
| home_smart_status_failed | The drive says it is failing | Replace it. Check backups first |
| home_pcie_aer_fatal | A PCIe device (often a GPU or NIC) hit a fatal bus error | `sudo dmesg -T \| grep -iE 'aer\|xid\|fallen off'`; a GPU that falls off the bus needs a full power cycle (2026-10-09) |
| home_systemd_unit_failed | A systemd service is in the failed state | `systemctl status <unit>`, `journalctl -u <unit> -n 50` |
| ecc_memory_* / 1hour_memory_hw_corrupted | ECC RAM errors (EPYC and Z4 have ECC) | Correctable: note the DIMM, watch. Uncorrectable: reseat/replace that DIMM |
| oom_kill | The kernel killed a process for lack of RAM in the last 30 min | `sudo dmesg -T \| grep -i 'killed process'` |
| ipmi_events / ipmi_sensor_state | The EPYC's BMC logged a hardware event or a sensor is out of range | `sudo ipmitool sel elist`; BMC web UI 10.0.0.159 |
| mdstat_* | Z4 RAID degraded/mismatch (the RAID rows check this too) | See nas-mount-problem |
| disk_space_usage / out_of_disk_space_time | A filesystem is filling, or will fill at the current rate | `df -h`, `du -xh --max-depth=1 /` |
| apps_group_file_descriptors_utilization | A program is close to its open-files limit (a leak) | The detail names the app group; `ls /proc/<pid>/fd \| wc -l` |
| system_reboot_detection | The machine rebooted recently | Expected after maintenance; otherwise check power and `journalctl -b -1 -e` |

## Silencing an alert

- A one-off that is understood: add `--ignore <alert name>` to the Netdata Alerts row in config/watchdog_dependency_checks.tsv.
- A rule that is wrong for this house: edit /etc/netdata/health.d/home.conf on the EPYC box, then `sudo netdatacli reload-health`.
- Changes already made: `/etc/netdata/health.d/disks.conf` on the EPYC is a copy of the stock file whose `10min_disk_backlog` skips md RAID arrays (`chart labels: device=!md* *`). The kernel's queue-time figure for an md array balloons whenever its disks are briefly busy (md1 read 740 s during a ~30 IOPS burst on 2026-10-14), so it warned about a healthy array. The real disks (sdb-sde) still have the alert; RAID health comes from the mdstat_* alerts and the Z4 RAID rows. To undo: `sudo rm /etc/netdata/health.d/disks.conf && sudo netdatacli reload-health`.
- A filesystem that should never be checked: add it to `exclude space metrics on paths` in that machine's /etc/netdata/netdata.conf (Pop!_OS's /recovery is already excluded).

## Layout

- Parent: EPYC box, /etc/netdata/netdata.conf (retention), stream.conf (accepts children from 10.* with one API key), health.d/home.conf (house alerts), health_alarm_notify.conf (notifications off).
- Children: /etc/netdata/netdata.conf keeps 1 hour in RAM and runs no alerts or ML of their own; stream.conf points at 10.0.0.188:19999. Their own web UI listens on localhost only.
- Installed from Netdata's apt repository, stable channel, automatic updates and telemetry off. Pop!_OS machines were installed as Ubuntu (22.04 main server, 24.04 EPYC and Z4). Not connected to Netdata Cloud.
- Uninstall a machine: `sudo apt purge 'netdata*'`.
