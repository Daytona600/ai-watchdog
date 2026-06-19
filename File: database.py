import sqlite3

DB_PATH = "/path/to/ai_watchdog.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create tables if they don't exist
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS update_monitor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            report TEXT,
            attention_needed TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS git_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            message TEXT,
            status TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

def insert_update_monitor(timestamp, report, attention_needed):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO update_monitor (timestamp, report, attention_needed)
        VALUES (?, ?, ?)
    ''', (timestamp, report, attention_needed))
    conn.commit()
    conn.close()

def insert_git_changes(timestamp, message, status):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO git_changes (timestamp, message, status)
        VALUES (?, ?, ?)
    ''', (timestamp, message, status))
    conn.commit()
    conn.close()

def get_update_monitor_history():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM update_monitor ORDER BY timestamp DESC')
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_git_changes_history():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM git_changes ORDER BY timestamp DESC')
    rows = cursor.fetchall()
    conn.close()
    return rows
