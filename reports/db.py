"""
SQLite 報表歷史資料庫
=====================
資料表: reports
路徑:   reports/reports.db
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "reports.db"

MONTH_ABBR = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr",
    5: "May", 6: "Jun", 7: "Jul", 8: "Aug",
    9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}
MONTH_ORDER = list(MONTH_ABBR.values())


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT    NOT NULL,
            report_type TEXT    NOT NULL DEFAULT 'daily',
            year        INTEGER NOT NULL,
            month       TEXT    NOT NULL,
            day         INTEGER NOT NULL,
            file_path   TEXT    NOT NULL,
            file_size   INTEGER,
            created_at  TEXT    NOT NULL,
            UNIQUE(report_date, report_type)
        )
    """)
    conn.commit()
    conn.close()


def register(report_date: str, report_type: str,
             year: int, month: str, day: int,
             file_path: str, file_size: int, created_at: str):
    """報表產生後呼叫此函式寫入 DB"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO reports
            (report_date, report_type, year, month, day, file_path, file_size, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (report_date, report_type, year, month, day, file_path, file_size, created_at))
    conn.commit()
    conn.close()


def query(year=None, month=None, report_type=None) -> list[dict]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM reports WHERE 1=1"
    params = []
    if year:
        sql += " AND year = ?"
        params.append(int(year))
    if month:
        sql += " AND month = ?"
        params.append(month)
    if report_type:
        sql += " AND report_type = ?"
        params.append(report_type)
    sql += " ORDER BY report_date DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_years() -> list[int]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT DISTINCT year FROM reports ORDER BY year DESC").fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_stats() -> dict:
    """首頁摘要用"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    total   = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    latest  = conn.execute("SELECT report_date FROM reports ORDER BY report_date DESC LIMIT 1").fetchone()
    by_type = conn.execute(
        "SELECT report_type, COUNT(*) FROM reports GROUP BY report_type"
    ).fetchall()
    conn.close()
    return {
        "total":   total,
        "latest":  latest[0] if latest else None,
        "by_type": {r[0]: r[1] for r in by_type},
    }
