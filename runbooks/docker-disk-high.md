# Docker Disk Usage High

Root disk or Docker storage is getting high.

Check first:

df -h /
sudo du -h -d1 /var/lib/docker 2>/dev/null | sort -h
docker system df
docker system df -v

Safer cleanup:

docker builder prune -af --filter "until=168h"
sudo journalctl --vacuum-time=14d

Avoid broad docker system prune -a unless you are sure.
