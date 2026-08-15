from pathlib import Path
import json
import re

DEFAULT_SEVERITY = "warning"
VALID_SEVERITIES = ("info", "warning", "critical")

_BASE = Path.home() / "ai-watchdog"
SEVERITY_MAP_PATH = _BASE / "config/severity_map.json"
SEVERITY_PATTERNS_PATH = _BASE / "config/watchdog_severity_patterns.tsv"


def _load_severity_map(path=SEVERITY_MAP_PATH):
    try:
        data = json.loads(path.read_text(errors="replace"))
    except Exception:
        return {}
    return {k: v for k, v in data.items() if v in VALID_SEVERITIES}


def _load_patterns(path=SEVERITY_PATTERNS_PATH):
    rules = []
    if not path.exists():
        return rules
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "\t" not in raw:
            continue
        pattern, key = raw.split("\t", 1)
        pattern = pattern.strip()
        key = key.strip()
        if pattern and key:
            rules.append((pattern, key))
    return rules


class SeverityClassifier:
    def __init__(self, severity_map_path=SEVERITY_MAP_PATH, patterns_path=SEVERITY_PATTERNS_PATH):
        self.severity_map = _load_severity_map(severity_map_path)
        self.rules = _load_patterns(patterns_path)

    def key_for(self, message: str):
        for pattern, key in self.rules:
            try:
                if re.search(pattern, message, flags=re.IGNORECASE):
                    return key
            except re.error:
                if pattern.lower() in message.lower():
                    return key
        return None

    def classify(self, message: str) -> str:
        key = self.key_for(message)
        if key is None:
            return DEFAULT_SEVERITY
        return self.severity_map.get(key, DEFAULT_SEVERITY)


_default_classifier = None


def classify(message: str) -> str:
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = SeverityClassifier()
    return _default_classifier.classify(message)


def is_alerting(severity: str) -> bool:
    return severity in ("warning", "critical")
