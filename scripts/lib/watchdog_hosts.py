from pathlib import Path
import re

_BASE = Path.home() / "ai-watchdog"
HOSTS_CONF_PATH = _BASE / "config" / "watchdog_known_hosts.conf"

# Keep these in sync with the defaults declared in each shell script that
# still sources config/watchdog_known_hosts.conf directly - this dict is
# only a fallback for when that file is missing/unreadable.
DEFAULTS = {
    "MAIN_SERVER_IP": "10.0.0.35",
    "HA_SERVER_IP": "10.0.0.30",
    "NAS_PRIMARY": "10.0.0.60",
    "NAS_SECONDARY": "10.0.0.6",
    "FRIGATE_HOST_IP": "10.0.0.85",
    "BEDROOM_LUNA_IP": "10.0.0.214",
    "LIVING_ROOM_IP": "10.0.0.66",
}

_ASSIGNMENT = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"?([^"\n]*)"?\s*$')


def _load(path=HOSTS_CONF_PATH):
    values = dict(DEFAULTS)
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ASSIGNMENT.match(line)
        if m:
            values[m.group(1)] = m.group(2)
    return values


_hosts = None


def hosts():
    global _hosts
    if _hosts is None:
        _hosts = _load()
    return _hosts


def get(name: str) -> str:
    return hosts().get(name, DEFAULTS.get(name, ""))


def expand(text: str) -> str:
    """Replace ${VAR} placeholders (e.g. in a TSV target column) with
    values from config/watchdog_known_hosts.conf."""
    values = hosts()

    def repl(m):
        return values.get(m.group(1), m.group(0))

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, text)
