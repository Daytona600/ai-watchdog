import json
import sys
import websocket

HA_URL = "ws://10.0.0.30:8123/api/websocket"
TOKEN = "REPLACED_TOKEN"


def connect():
    ws = websocket.create_connection(HA_URL, timeout=15)
    msg = json.loads(ws.recv())
    assert msg["type"] == "auth_required", msg
    ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
    msg = json.loads(ws.recv())
    assert msg["type"] == "auth_ok", msg
    return ws


def call(ws, msg_id, payload):
    payload["id"] = msg_id
    ws.send(json.dumps(payload))
    while True:
        resp = json.loads(ws.recv())
        if resp.get("id") == msg_id:
            return resp


def main():
    cmd = sys.argv[1]
    ws = connect()

    if cmd == "add_resource":
        url = sys.argv[2]
        resp = call(ws, 1, {"type": "lovelace/resources/create", "url": url, "res_type": "module"})
        print(json.dumps(resp, indent=2))

    elif cmd == "delete_resource":
        resource_id = sys.argv[2]
        resp = call(ws, 1, {"type": "lovelace/resources/delete", "resource_id": resource_id})
        print(json.dumps(resp, indent=2))

    elif cmd == "list_resources":
        resp = call(ws, 1, {"type": "lovelace/resources"})
        print(json.dumps(resp, indent=2))

    elif cmd == "list_dashboards":
        resp = call(ws, 1, {"type": "lovelace/dashboards/list"})
        print(json.dumps(resp, indent=2))

    elif cmd == "create_dashboard":
        url_path = sys.argv[2]
        title = sys.argv[3]
        icon = sys.argv[4] if len(sys.argv) > 4 else None
        payload = {
            "type": "lovelace/dashboards/create",
            "url_path": url_path,
            "title": title,
            "mode": "storage",
            "show_in_sidebar": True,
            "require_admin": False,
        }
        if icon:
            payload["icon"] = icon
        resp = call(ws, 1, payload)
        print(json.dumps(resp, indent=2))

    elif cmd == "get_config":
        url_path = sys.argv[2] if len(sys.argv) > 2 else None
        payload = {"type": "lovelace/config"}
        if url_path:
            payload["url_path"] = url_path
        resp = call(ws, 1, payload)
        print(json.dumps(resp, indent=2))

    elif cmd == "save_config":
        url_path = sys.argv[2] if sys.argv[2] != "-" else None
        config_file = sys.argv[3]
        with open(config_file, encoding="utf-8") as f:
            config = json.load(f)
        payload = {"type": "lovelace/config/save", "config": config}
        if url_path:
            payload["url_path"] = url_path
        resp = call(ws, 1, payload)
        print(json.dumps(resp, indent=2))

    elif cmd == "list_input_select":
        resp = call(ws, 1, {"type": "input_select/list"})
        print(json.dumps(resp, indent=2))

    elif cmd == "clear_input_select_initial":
        # input_select/update requires the FULL config (name, options, icon)
        # even to change one field - not a partial patch. Fetches the
        # current config first, then re-sends it with "initial" omitted
        # entirely (must be omitted, not null - the schema rejects null).
        # Use this to fix an input_select helper whose selection resets to
        # a stale default every time HA restarts - HA's input_select
        # platform always applies a stored "initial" value on boot,
        # overriding whatever was actually last selected.
        input_select_id = sys.argv[2]
        listing = call(ws, 1, {"type": "input_select/list"})
        current = next(x for x in listing["result"] if x["id"] == input_select_id)
        payload = {
            "type": "input_select/update",
            "input_select_id": input_select_id,
            "name": current["name"],
            "options": current["options"],
        }
        if "icon" in current:
            payload["icon"] = current["icon"]
        resp = call(ws, 2, payload)
        print(json.dumps(resp, indent=2))

    elif cmd == "add_input_select_option":
        # input_select/update requires the FULL config (name, options, icon)
        # even to add one option - not a partial patch (same gotcha as
        # clear_input_select_initial above). A plain input_select.set_options
        # SERVICE call only updates the live entity state, not the stored
        # helper config - the new option silently disappears on the next HA
        # restart unless this websocket update is also done. Preserves
        # "initial" if the helper already has one, so this doesn't
        # reintroduce the stale-default-on-restart bug for helpers where
        # that's already been cleared.
        input_select_id = sys.argv[2]
        new_option = sys.argv[3]
        listing = call(ws, 1, {"type": "input_select/list"})
        current = next(x for x in listing["result"] if x["id"] == input_select_id)
        options = list(current["options"])
        if new_option not in options:
            options.append(new_option)
        payload = {
            "type": "input_select/update",
            "input_select_id": input_select_id,
            "name": current["name"],
            "options": options,
        }
        if "icon" in current:
            payload["icon"] = current["icon"]
        if "initial" in current:
            payload["initial"] = current["initial"]
        resp = call(ws, 2, payload)
        print(json.dumps(resp, indent=2))

    ws.close()


if __name__ == "__main__":
    main()
