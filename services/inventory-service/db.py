import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

DB_PATH = os.environ.get("DB_PATH", "/app/data/inventory.db")

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode      TEXT UNIQUE,
    name         TEXT NOT NULL,
    brand        TEXT,
    category     TEXT,
    quantity     REAL NOT NULL DEFAULT 0,
    unit         TEXT NOT NULL DEFAULT 'each',
    location     TEXT,
    par_level    REAL NOT NULL DEFAULT 0,
    expiry_date  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_barcode ON items(barcode) WHERE barcode IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);
CREATE INDEX IF NOT EXISTS idx_items_expiry ON items(expiry_date);

CREATE TABLE IF NOT EXISTS inventory_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id         INTEGER REFERENCES items(id) ON DELETE SET NULL,
    barcode         TEXT,
    item_name       TEXT NOT NULL,
    event_type      TEXT NOT NULL CHECK (event_type IN
                        ('scan_in','scan_out','manual_adjust','create','update','delete')),
    quantity_delta  REAL NOT NULL DEFAULT 0,
    quantity_after  REAL,
    source          TEXT NOT NULL DEFAULT 'api',
    note            TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_events_item_id ON inventory_events(item_id);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON inventory_events(created_at);

CREATE TABLE IF NOT EXISTS barcode_cache (
    barcode     TEXT PRIMARY KEY,
    found       INTEGER NOT NULL,
    name        TEXT,
    brand       TEXT,
    category    TEXT,
    raw_json    TEXT,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def get_item(conn: sqlite3.Connection, item_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def get_item_by_barcode(conn: sqlite3.Connection, barcode: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM items WHERE barcode = ?", (barcode,)).fetchone()


def list_items(
    conn: sqlite3.Connection,
    category: Optional[str] = None,
    location: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[sqlite3.Row]:
    clauses = []
    params: list[Any] = []
    if category:
        clauses.append("category = ?")
        params.append(category)
    if location:
        clauses.append("location = ?")
        params.append(location)
    if search:
        clauses.append("name LIKE ?")
        params.append(f"%{search}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    return conn.execute(
        f"SELECT * FROM items {where} ORDER BY name COLLATE NOCASE LIMIT ? OFFSET ?",
        params,
    ).fetchall()


def list_expiring(conn: sqlite3.Connection, days: int = 7) -> list[sqlite3.Row]:
    cutoff = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    return conn.execute(
        """
        SELECT * FROM items
        WHERE expiry_date IS NOT NULL AND expiry_date <= ?
        ORDER BY expiry_date ASC
        """,
        (cutoff,),
    ).fetchall()


def list_low_stock(conn: sqlite3.Connection, location: Optional[str] = None) -> list[sqlite3.Row]:
    clauses = ["par_level > 0", "quantity <= par_level"]
    params: list[Any] = []
    if location:
        clauses.append("location = ?")
        params.append(location)
    where = " AND ".join(clauses)
    return conn.execute(
        f"""
        SELECT *, (par_level - quantity) AS deficit
        FROM items
        WHERE {where}
        ORDER BY deficit DESC
        """,
        params,
    ).fetchall()


def create_item(conn: sqlite3.Connection, **fields: Any) -> sqlite3.Row:
    cols = list(fields.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    cur = conn.execute(
        f"INSERT INTO items ({col_list}) VALUES ({placeholders})",
        [fields[c] for c in cols],
    )
    conn.commit()
    return get_item(conn, cur.lastrowid)


def update_item(conn: sqlite3.Connection, item_id: int, **fields: Any) -> Optional[sqlite3.Row]:
    if not fields:
        return get_item(conn, item_id)
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE items SET {set_clause} WHERE id = ?",
        [*fields.values(), item_id],
    )
    conn.commit()
    return get_item(conn, item_id)


def delete_item(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()


def log_event(
    conn: sqlite3.Connection,
    item_id: Optional[int],
    barcode: Optional[str],
    item_name: str,
    event_type: str,
    quantity_delta: float = 0,
    quantity_after: Optional[float] = None,
    source: str = "api",
    note: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO inventory_events
            (item_id, barcode, item_name, event_type, quantity_delta, quantity_after, source, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (item_id, barcode, item_name, event_type, quantity_delta, quantity_after, source, note),
    )
    conn.commit()


def get_barcode_cache(conn: sqlite3.Connection, barcode: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM barcode_cache WHERE barcode = ?", (barcode,)).fetchone()


def set_barcode_cache(
    conn: sqlite3.Connection,
    barcode: str,
    found: bool,
    name: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    raw_json: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO barcode_cache (barcode, found, name, brand, category, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(barcode) DO UPDATE SET
            found = excluded.found,
            name = excluded.name,
            brand = excluded.brand,
            category = excluded.category,
            raw_json = excluded.raw_json,
            fetched_at = datetime('now')
        """,
        (barcode, 1 if found else 0, name, brand, category, raw_json),
    )
    conn.commit()
