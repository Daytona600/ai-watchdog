# Backup Problem

A nightly backup is failing or has not run. Seven machines each keep two independent copies using restic (7 daily, 4 weekly, 3 monthly snapshots):

- Main server (10.0.0.35): /home/davids/backup.sh, cron 02:00, runs as root. Copies: /mnt/nas1/pop-os-backup (the Z4) and /mnt/nas2/pop-os-backup (10.0.0.6). Log: /home/davids/restic-backup.log
- Z4 (10.0.0.60): /usr/local/sbin/host-backup, systemd host-backup.timer 03:30. Copies: /mnt/recovered/backup/z4-backup (its own RAID) and /mnt/nas2/z4-backup (10.0.0.6). Log: /var/log/host-backup.log
- EPYC box (10.0.0.188, ssh alias apex-agent-second): host-backup, host-backup.timer 02:45. Copies: /mnt/nas1/epyc-backup (the Z4) and /mnt/nas2/epyc-backup (10.0.0.6). Log: /var/log/host-backup.log
- Sky camera box (10.0.0.63, hostname satellite-4, ssh alias apex-agent-skycam): host-backup, host-backup.timer 10:15 every morning (so it never overlaps the night's capture or the 13:00 retention run). Copies: /mnt/nas1/skycam-backup (the Z4) and /mnt/nas2/skycam-backup (10.0.0.6). Log: /var/log/host-backup.log
- Living room satellite (10.0.0.66, hostname front-room-satellite, ssh alias apex-agent-livingroom): host-backup, host-backup.timer 03:50. Copies: /mnt/nas1/livingroom-backup (the Z4) and /mnt/nas2/livingroom-backup (10.0.0.6). Log: /var/log/host-backup.log
- Bedroom satellite (10.0.0.214, hostname bedroom-voice-satellite, ssh alias apex-agent-bedroom): host-backup, host-backup.timer 04:10. Copies: /mnt/nas1/bedroom-backup and /mnt/nas2/bedroom-backup. Log: /var/log/host-backup.log
- Jellyfin / kitchen satellite PC (10.0.0.107, hostname jellyfin, ssh alias apex-agent-jellyfin): host-backup, host-backup.timer 04:30. Copies: /mnt/nas1/jellyfin-backup and /mnt/nas2/jellyfin-backup. Log: /var/log/host-backup.log

Home Assistant backs itself up separately to the HAbackup share on the Z4 and on 10.0.0.6 (see the HA backup stale runbook). The Z4's NAS data is mirrored to 10.0.0.6 nightly by ~/nas-backup-sync.sh.

The dashboard check reads /var/lib/host-backup/status.json on each machine. It complains when a repository has had no good backup for 50 hours, so one missed night is tolerated but two in a row are not.

See what each machine last did:

ssh apex-agent-z4 'cat /var/lib/host-backup/status.json'

ssh apex-agent-second 'cat /var/lib/host-backup/status.json'

ssh apex-agent-skycam 'cat /var/lib/host-backup/status.json'

ssh apex-agent-livingroom 'cat /var/lib/host-backup/status.json'

ssh apex-agent-bedroom 'cat /var/lib/host-backup/status.json'

ssh apex-agent-jellyfin 'cat /var/lib/host-backup/status.json'

cat /var/lib/host-backup/status.json

Common causes:

- A NAS share is not mounted (status says "... is not mounted"): findmnt /mnt/nas1 /mnt/nas2, then sudo mount -a. A backup never writes to a repository path whose mount is missing.
- "permission denied" opening a repository: backups must run as root. Since the Z4 stopped squashing NFS users (2026-08-25) a repository cannot be written by two different users. This is what silently broke the main server's backups for six weeks.
- "ciphertext verification failed": the repository config does not match its keys, or the password is wrong. Do not re-initialise over it. Set the old directory aside (mv), create a new repository next to it, and keep the old one until the new one has snapshots.
- A repository is locked by a run that is still going: wait, or use restic unlock only if no backup process is running.

Run a backup by hand (the same thing the timer does):

sudo /usr/local/sbin/host-backup

On the main server:

sudo /home/davids/backup.sh

Check every repository for corruption (any machine except the main server):

sudo /usr/local/sbin/host-backup --check

Restore: any machine with restic and the NAS shares mounted can restore. The restic password is the same for every repository: it is in /root/.restic-password on every machine except the main server (Z4, EPYC box, sky camera, both satellites, Jellyfin PC), and inside /home/davids/backup.sh on the main server. KEEP A COPY OF THAT PASSWORD OUTSIDE THESE MACHINES (a password manager). Without it none of the backups can be opened, and the copy inside the backups themselves does not help if every machine is lost.

List snapshots, then restore one file or directory to /restore:

sudo restic -r /mnt/nas2/z4-backup --password-file /root/.restic-password snapshots

sudo restic -r /mnt/nas2/z4-backup --password-file /root/.restic-password restore latest --target /restore --include /etc/fstab

Each backup also contains /var/backups/host-inventory: partition tables, RAID layout, installed packages, enabled services, Docker, GPU and BMC settings, the list of Ollama models to re-download, and consistent copies of the live SQLite databases (Frigate, OpenCode). RESTORE-NOTES.txt in that folder walks through a full rebuild.

Not backed up on purpose because it can be downloaded again: container images, Ollama models, ROCm, caches. The Z4's recordings are covered by the nightly mirror to 10.0.0.6, not by restic.

Also left out on the main server (about 240 GB that can be downloaded or regenerated): the ComfyUI models (/opt/comfyui/app/data/models and text_encoders) and the micro wake word training datasets, work folder and virtualenvs (/opt/microwakeword-training/training_datasets, work, .venv, .recorder-venv). Still backed up: the trained wake word models (output/), the recorded voice samples (personal_samples/), the training tools and the ComfyUI workflows. The exclusions are in the restic command in /home/davids/backup.sh.

On the sky camera box the nightly frames, anomaly captures and rendered videos (/home/david/skycam/frames, anomalies and timelapses) are not in restic: skycam-retention archives them to the Z4 and 10.0.0.6 after 12 days, and nas_sync.sh copies the daily timelapses to both. Backed up: the software in /opt/skycam and /var/lib/skycam, its settings, the dark-frame calibration, the camera stack under /usr/local and the udev rules.

The two voice satellites and the Jellyfin PC back up /home/david (pipeline, satellite and wake-word code and config, user systemd units), /etc and /opt. Not in restic: caches, browser profiles, OpenCode's downloaded program, Docker images (rebuilt from the compose projects), and on the Jellyfin PC the media itself (on the Z4 share /mnt/nas/public) plus Jellyfin's cache and logs. The Jellyfin database is saved as a consistent copy under /var/backups/host-inventory/sqlite.
