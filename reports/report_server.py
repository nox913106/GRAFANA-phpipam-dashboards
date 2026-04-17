#!/usr/bin/env python3
"""
phpIPAM 巡檢報表歷史瀏覽伺服器
================================
Port  : 8088
存取  : http://<server-ip>:8088  (限內網 172./192.168./10.)
啟動  : python3 report_server.py
systemd: sudo cp phpipam-report.service /etc/systemd/system/
         sudo systemctl enable --now phpipam-report
"""

import sys
from pathlib import Path
from flask import Flask, send_file, jsonify, render_template, request, abort
import db as report_db

SCRIPT_DIR   = Path(__file__).parent
ARCHIVE_DIR  = SCRIPT_DIR / "archive"

app = Flask(__name__, template_folder=str(SCRIPT_DIR / "template"))

# ── 內網限制 ────────────────────────────────────────────
ALLOWED = ("172.", "192.168.", "10.", "127.")

@app.before_request
def internal_only():
    ip = request.remote_addr or ""
    if not any(ip.startswith(p) for p in ALLOWED):
        abort(403)


# ════════════════════════════════════════════════════════
# 前端
# ════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


# ════════════════════════════════════════════════════════
# 靜態報表 HTML
# 路徑格式: /archive/2026/Apr/17/daily_report_20260417.html
# ════════════════════════════════════════════════════════

@app.route("/archive/<path:filepath>")
def serve_archive(filepath):
    full = (ARCHIVE_DIR / filepath).resolve()
    # 防止路徑穿越
    if not str(full).startswith(str(ARCHIVE_DIR.resolve())):
        abort(403)
    if not full.exists() or not full.is_file():
        abort(404)
    return send_file(str(full), mimetype="text/html")


# ════════════════════════════════════════════════════════
# API
# ════════════════════════════════════════════════════════

@app.route("/api/reports")
def api_reports():
    """
    GET /api/reports
    Query params: year, month, type (daily|weekly|monthly)
    """
    reports = report_db.query(
        year=request.args.get("year"),
        month=request.args.get("month"),
        report_type=request.args.get("type"),
    )
    return jsonify(reports)


@app.route("/api/years")
def api_years():
    return jsonify(report_db.get_years())


@app.route("/api/stats")
def api_stats():
    return jsonify(report_db.get_stats())


# ════════════════════════════════════════════════════════
# 啟動
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        from flask import Flask as _check  # noqa
    except ImportError:
        print("ERROR: 需要安裝 Flask: pip3 install flask")
        sys.exit(1)

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    report_db.init_db()
    print("phpIPAM Report Server 啟動中...")
    print(f"  Archive : {ARCHIVE_DIR}")
    print(f"  DB      : {report_db.DB_PATH}")
    print(f"  URL     : http://0.0.0.0:8088")
    app.run(host="0.0.0.0", port=8088, debug=False)
