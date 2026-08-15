#!/usr/bin/env python3
import logging, os, time
import requests
from evdev import InputDevice, categorize, ecodes

DEVICE_PATH   = "/dev/input/by-id/usb-ARM_CM0_USB_HID_Keyboard_0123456789AB-event-kbd"
HA_BASE_URL   = "http://10.0.0.30:8123"
HA_TOKEN      = os.environ["HA_TOKEN"]
INVENTORY_URL = "http://localhost:4000"
SATELLITES    = {
    "living_room": "http://10.0.0.71:3994/speak",
    "bedroom":     "http://10.0.0.123:3994/speak",
}
BEDROOM_ARM_HEADSET_URL = "http://10.0.0.123:3994/arm_headset"
HEADSET_ARM_TTL = 10  # seconds -- short window, confirmations are near-instant
HTTP_TIMEOUT  = 5

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scan-listener")

KEYMAP = {
    "KEY_1": ("1", "!"), "KEY_2": ("2", "@"), "KEY_3": ("3", "#"),
    "KEY_4": ("4", "$"), "KEY_5": ("5", "%"), "KEY_6": ("6", "^"),
    "KEY_7": ("7", "&"), "KEY_8": ("8", "*"), "KEY_9": ("9", "("),
    "KEY_0": ("0", ")"),
    "KEY_KP0": ("0","0"), "KEY_KP1": ("1","1"), "KEY_KP2": ("2","2"),
    "KEY_KP3": ("3","3"), "KEY_KP4": ("4","4"), "KEY_KP5": ("5","5"),
    "KEY_KP6": ("6","6"), "KEY_KP7": ("7","7"), "KEY_KP8": ("8","8"),
    "KEY_KP9": ("9","9"),
    "KEY_MINUS": ("-", "_"), "KEY_KPMINUS": ("-", "-"),
    **{f"KEY_{c}": (c.lower(), c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
}
ENTER_KEYS = {"KEY_ENTER", "KEY_KPENTER"}
SHIFT_KEYS = {"KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"}


def open_device() -> InputDevice:
    """Block/retry until the dongle's device node exists and opens (survives unplug/replug)."""
    while True:
        try:
            dev = InputDevice(DEVICE_PATH)
            log.info("Opened scanner device %s (%s)", DEVICE_PATH, dev.name)
            # Deliberately not calling dev.grab() -- headless server, no foreground
            # app for scans to "leak" into, and not grabbing keeps evtest usable
            # concurrently for troubleshooting.
            return dev
        except (FileNotFoundError, OSError) as e:
            log.warning("Scanner device not available (%s); retrying in 5s", e)
            time.sleep(5)


def get_scan_mode() -> str:
    try:
        r = requests.get(f"{HA_BASE_URL}/api/states/input_select.inventory_scan_mode",
                          headers={"Authorization": f"Bearer {HA_TOKEN}"}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return "out" if r.json().get("state") == "Scan Out" else "in"
    except Exception:
        log.exception("Failed to read scan mode from HA; defaulting to Scan In")
        return "in"


def get_speaker_targets() -> tuple[list[str], bool]:
    """Returns (list of satellite /speak URLs to announce on, whether to arm
    the bedroom headset first), based on the input_select.inventory_scan_speakers
    helper. Fails safe to 'both' on any HA-API error so a confirmation is never
    silently lost."""
    try:
        r = requests.get(f"{HA_BASE_URL}/api/states/input_select.inventory_scan_speakers",
                          headers={"Authorization": f"Bearer {HA_TOKEN}"}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        state = r.json().get("state", "Both")
    except Exception:
        log.exception("Failed to read speaker target from HA; defaulting to Both")
        state = "Both"

    if state == "None":
        return [], False
    if state == "Headset Only":
        return [SATELLITES["bedroom"]], True
    if state == "Living Room Only":
        return [SATELLITES["living_room"]], False
    if state == "Bedroom Only":
        return [SATELLITES["bedroom"]], False
    return list(SATELLITES.values()), False


def arm_headset() -> None:
    try:
        requests.post(BEDROOM_ARM_HEADSET_URL, json={"ttl": HEADSET_ARM_TTL}, timeout=HTTP_TIMEOUT)
    except Exception:
        log.exception("Failed to arm headset -- confirmation may play on the room speaker instead")


def call_inventory(mode: str, barcode: str):
    endpoint = "scan-in" if mode == "in" else "scan-out"
    try:
        r = requests.post(f"{INVENTORY_URL}/{endpoint}",
                           json={"barcode": barcode, "quantity": 1}, timeout=HTTP_TIMEOUT)
        if r.status_code == 404 and mode == "out":
            return None, "unknown_barcode"
        r.raise_for_status()
        return r.json(), None
    except Exception:
        log.exception("inventory-service call failed: %s barcode=%s", endpoint, barcode)
        return None, "service_down"


def build_phrase(mode: str, result, error) -> str:
    if error == "unknown_barcode":
        return "That item hasn't been scanned in yet, so there's nothing to remove."
    if error == "service_down":
        return "Sorry, the inventory service isn't responding right now."
    item = (result or {}).get("item", {})
    name, qty = item.get("name"), item.get("quantity")
    lookup_source = (result or {}).get("lookup_source", "")
    if mode == "in":
        if lookup_source.startswith("stub"):
            return "Added a new item, please rename it on the dashboard."
        return f"Added {name}, now have {qty}."
    return f"Used one {name}, {qty} left." if name else f"Used one, {qty} left."


def speak_targets(text: str, targets: list[str]) -> None:
    for url in targets:
        try:
            requests.post(url, json={"text": text, "persona": "david", "enhance": False,
                                      "no_mute": True, "duck": False}, timeout=HTTP_TIMEOUT)
        except Exception:
            log.exception("Speak request failed for %s", url)


def handle_scan(barcode: str) -> None:
    log.info("Scanned barcode: %s", barcode)
    try:
        mode = get_scan_mode()
        result, error = call_inventory(mode, barcode)
        targets, use_headset = get_speaker_targets()
        if use_headset:
            arm_headset()
        speak_targets(build_phrase(mode, result, error), targets)
    except Exception:
        log.exception("Unhandled error processing scan %r", barcode)


def main() -> None:
    dev = open_device()
    buffer: list[str] = []
    shift_down = False
    while True:
        try:
            for event in dev.read_loop():
                if event.type != ecodes.EV_KEY:
                    continue
                key = categorize(event)
                code = key.keycode if isinstance(key.keycode, str) else key.keycode[0]
                if code in SHIFT_KEYS:
                    shift_down = key.keystate in (key.key_down, key.key_hold)
                    continue
                if key.keystate != key.key_down:   # ignore keyup; scanners send fast down+up pairs
                    continue
                if code in ENTER_KEYS:
                    if buffer:
                        barcode, buffer[:] = "".join(buffer), []
                        handle_scan(barcode)
                    continue
                pair = KEYMAP.get(code)
                if pair:
                    buffer.append(pair[1] if shift_down else pair[0])
                else:
                    log.debug("Ignoring unmapped key %s", code)
        except OSError:
            log.exception("Lost connection to scanner device; reopening")
            time.sleep(2)
            dev = open_device()
            buffer.clear()
        except Exception:
            log.exception("Unexpected error in scanner read loop; continuing")
            time.sleep(1)


if __name__ == "__main__":
    main()
