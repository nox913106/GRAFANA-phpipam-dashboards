#!/usr/bin/env python3
"""
phpIPAM 日巡檢 HTML 報表產生器
================================
用法:
  # 使用範例資料預覽排版（不儲存至 archive）
  python3 report_generator.py --sample

  # 使用指定 JSON 資料檔產生報表（儲存至 archive + 寫入 DB）
  python3 report_generator.py --data path/to/data.json

  # 跳過 AI 分析
  python3 report_generator.py --data data.json --no-ai

輸出: archive/YYYY/Mon/DD/daily_report_YYYYMMDD.html
DB:   reports.db
"""

import json
import os
import sys
import argparse
import logging
from datetime import datetime
from pathlib import Path

import db as report_db

# ── 路徑設定 ────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent
TEMPLATE_DIR = SCRIPT_DIR / "template"
ARCHIVE_DIR  = SCRIPT_DIR / "archive"
SAMPLE_DATA  = SCRIPT_DIR / "sample_data.json"

MONTH_ABBR = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr",
    5: "May", 6: "Jun", 7: "Jul", 8: "Aug",
    9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════
# 1. Jinja2 渲染
# ════════════════════════════════════════════════════════

def render_template(data: dict) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError:
        logger.error("需要安裝 Jinja2: pip3 install jinja2")
        sys.exit(1)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("daily_report.html")
    return template.render(**data)


# ════════════════════════════════════════════════════════
# 2. Archive 儲存 + DB 註冊
# ════════════════════════════════════════════════════════

def save_to_archive(html: str, report_date: str, report_type: str = "daily") -> Path:
    """
    儲存至 archive/YYYY/Mon/DD/
    回傳完整路徑
    """
    d = datetime.strptime(report_date, "%Y-%m-%d")
    year  = d.year
    month = MONTH_ABBR[d.month]
    day   = d.day

    out_dir = ARCHIVE_DIR / str(year) / month / str(day)
    out_dir.mkdir(parents=True, exist_ok=True)

    date_str  = report_date.replace("-", "")
    filename  = f"{report_type}_report_{date_str}.html"
    out_path  = out_dir / filename
    out_path.write_text(html, encoding="utf-8")

    # 相對於 archive/ 的路徑（供 Flask route 使用）
    rel_path = f"{year}/{month}/{day}/{filename}"

    report_db.register(
        report_date=report_date,
        report_type=report_type,
        year=year,
        month=month,
        day=day,
        file_path=rel_path,
        file_size=out_path.stat().st_size,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return out_path


def save_preview(html: str, report_date: str) -> Path:
    """--sample 模式：輸出到 output/ 預覽，不寫入 DB"""
    out_dir = SCRIPT_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = report_date.replace("-", "")
    out_path = out_dir / f"daily_report_{date_str}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


# ════════════════════════════════════════════════════════
# 3. Claude AI 分析
# ════════════════════════════════════════════════════════

def build_analysis_prompt(data: dict) -> str:
    return f"""你是一位資深網路工程師，請根據以下 phpIPAM 日巡檢數據，以繁體中文（台灣）撰寫：

1. summary：一段 2-3 句話的整體摘要（整體狀況 + 最需關注的事項）
2. focus_points：3-5 個關注重點（條列式，具體描述異常或風險）
3. suggestions：3-5 個改善建議（條列式，具體可執行的行動）

---
【全局概覽】
- 總 IP 數量: {data['overview']['total_ip']}
- Active IP: {data['overview']['active_ip']}
- DHCP Pool IP: {data['overview']['dhcp_pool_ip']}

【IP 異動（24hr）】
- 總異動: {data['changes']['total']} 筆（MODIFY {data['changes']['modify']} / ADD {data['changes'].get('add',0)} / DELETED {data['changes']['deleted']}）
- 活躍操作者: {data['changes']['operators']} 人
- 高頻異動 IP: {json.dumps(data['hot_ips'], ensure_ascii=False)}
- 子網路異動: {json.dumps(data['subnet_changes'], ensure_ascii=False)}

【高使用率網段（>80%）】
{json.dumps(data['high_usage_detail'], ensure_ascii=False, indent=2)}

---

請以 JSON 格式回覆（只回覆 JSON，不要其他說明文字）：
{{
  "summary": "...",
  "focus_points": ["...", "..."],
  "suggestions":  ["...", "..."]
}}"""


def call_claude_api(prompt: str) -> dict:
    try:
        import anthropic
    except ImportError:
        logger.error("需要安裝 anthropic SDK: pip3 install anthropic")
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("請設定環境變數 ANTHROPIC_API_KEY")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    logger.info("呼叫 Claude API 進行 AI 分析...")

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def placeholder_analysis() -> dict:
    return {
        "summary": "（AI 分析已停用，請設定 ANTHROPIC_API_KEY 後以 --data 模式重新產生）",
        "focus_points": ["請啟用 AI 分析以取得關注重點"],
        "suggestions":  ["請啟用 AI 分析以取得改善建議"],
    }


# ════════════════════════════════════════════════════════
# 4. 主流程
# ════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="phpIPAM 日巡檢 HTML 報表產生器")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sample", action="store_true",
                       help="使用內建範例資料（輸出至 output/，不寫入 DB）")
    group.add_argument("--data", type=str, metavar="FILE",
                       help="指定 JSON 資料檔路徑（輸出至 archive/，寫入 DB）")
    parser.add_argument("--no-ai", action="store_true", help="跳過 AI 分析")
    parser.add_argument("--type", default="daily",
                        choices=["daily", "weekly", "monthly"],
                        help="報表類型（預設: daily）")
    args = parser.parse_args()

    # 1. 載入資料
    if args.sample:
        logger.info(f"載入範例資料: {SAMPLE_DATA}")
        with open(SAMPLE_DATA, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data_path = Path(args.data)
        if not data_path.exists():
            logger.error(f"找不到資料檔: {data_path}")
            sys.exit(1)
        logger.info(f"載入資料: {data_path}")
        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    if "generated_at" not in data:
        data["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 2. AI 分析
    if args.no_ai:
        data["ai_analysis"] = placeholder_analysis()
        logger.info("AI 分析：已跳過")
    elif args.sample:
        # sample 模式：直接使用 JSON 內建的 ai_analysis（示範用）
        if "ai_analysis" not in data:
            data["ai_analysis"] = placeholder_analysis()
        logger.info("AI 分析：使用範例資料內建內容")
    else:
        data["ai_analysis"] = call_claude_api(build_analysis_prompt(data))
        logger.info("AI 分析完成")

    # 3. 渲染
    logger.info("渲染 HTML 模板...")
    html = render_template(data)

    # 4. 儲存
    report_date = data.get("report_date", datetime.now().strftime("%Y-%m-%d"))
    if args.sample:
        out_path = save_preview(html, report_date)
        logger.info(f"預覽報表（未寫入 DB）: {out_path}")
    else:
        out_path = save_to_archive(html, report_date, args.type)
        logger.info(f"報表已存入 archive + DB: {out_path}")

    print(f"\n✅ 輸出路徑: {out_path}")


if __name__ == "__main__":
    main()
