import sys
from datetime import datetime
from watchdog_change_window_v1 import now_iso, stamp, safe_label, run, sha256_text, parse_env, http_json, read_public_json, capture_ha, capture_docker, capture_nodered, capture_frigate, capture_apt, capture_public, capture, set_diff, compare_dict, load_summary, compare, add_attention
from database import Database

def main():
    db = Database()
    db.create_collection()

    # Example usage of the database
    stage_name = "Initial Setup"
    description = "Setting up initial configuration"
    db.add_stage(stage_name, description)

    last_stage = db.get_last_stage()
    if last_stage:
        print(f"Last stage: {last_stage['stage_name']} - {last_stage['description']}")
    else:
        print("No stages found.")

if __name__ == '__main__':
    main()
