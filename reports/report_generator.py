#!/usr/bin/env python3
"""
phpIPAM 日巡檢 HTML 報表產生器
================================
用法:
  # 使用範例資料預覽排版
  python3 report_generator.py --sample

  # 使用指定 JSON 資料檔產生報表
  python3 report_generator.py --data path/to/data.json

  # 使用範例資料 + 跳過 AI 分析（直接填入佔位文字）
  python3 report_generator.py --sample --no-ai

輸出: reports/output/daily_report_YYYYMMDD.html
"""

import json
import os
import sys
import argparse
import logging
from datetime import datetime
from pathlib import Path

# ── 路徑設定 ────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent
TEMPLATE_DIR = SCRIPT_DIR / "template"
OUTPUT_DIR   = SCRIPT_DIR / "output"
SAMPLE_DATA  = SCRIPT_DIR / "sample_data.json"

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
        logger.error("需要安裝 Jinja2: pip install jinja2")
        sys.exit(1)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("daily_report.html")
    return template.render(**data)


# ════════════════════════════════════════════════════════
# 2. Claude AI 分析（claude-sonnet-4-6）
# ════════════════════════════════════════════════════════

def build_analysis_prompt(data: dict) -> str:
    """將報表數據整理成給 Claude 的分析 Prompt"""
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
- 總異動: {data['changes']['total']} 筆（MODIFY {data['changes']['modify']} / ADD {data['changes']['add']} / DELETED {data['changes']['deleted']}）
- 活躍操作者: {data['changes']['operators']} 人
- 高頻異動 IP TOP 5: {json.dumps(data['hot_ips'], ensure_ascii=False)}
- 子網路異動摘要: {json.dumps(data['subnet_changes'], ensure_ascii=False)}

【高使用率網段（>80%）】
{json.dumps(data['high_usage_detail'], ensure_ascii=False, indent=2)}

【DHCP Server 健康度】
{json.dumps(data['dhcp_stats'], ensure_ascii=False, indent=2)}
---

請以 JSON 格式回覆，結構如下：
{{
  "summary": "...",
  "focus_points": ["...", "...", "..."],
  "suggestions": ["...", "...", "..."]
}}
只回覆 JSON，不要其他說明文字。"""


def call_claude_api(prompt: str) -> dict:
    """呼叫 Claude API 取得 AI 分析結果"""
    try:
        import anthropic
    except ImportError:
        logger.error("需要安裝 anthropic SDK: pip install anthropic")
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("請設定環境變數 ANTHROPIC_API_KEY")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    logger.info("正在呼叫 Claude API 進行 AI 分析...")

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # 去除可能的 ```json ... ``` 包裝
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw.strip())


def placeholder_analysis() -> dict:
    """--no-ai 時填入佔位文字"""
    return {
        "summary": "（AI 分析已停用，請以 --sample 模式執行或設定 ANTHROPIC_API_KEY 後重新產生）",
        "focus_points": ["請啟用 AI 分析以取得關注重點"],
        "suggestions":  ["請啟用 AI 分析以取得改善建議"],
    }


# ════════════════════════════════════════════════════════
# 3. 主流程
# ════════════════════════════════════════════════════════

def load_data(data_path: Path) -> dict:
    with open(data_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_report(html: str, report_date: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = OUTPUT_DIR / f"daily_report_{report_date.replace('-', '')}.html"
    filename.write_text(html, encoding="utf-8")
    return filename


def main():
    parser = argparse.ArgumentParser(description="phpIPAM 日巡檢 HTML 報表產生器")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sample", action="store_true",  help="使用內建範例資料")
    group.add_argument("--data",   type=str, metavar="FILE", help="指定 JSON 資料檔路徑")
    parser.add_argument("--no-ai", action="store_true",  help="跳過 AI 分析，填入佔位文字")
    args = parser.parse_args()

    # 1. 載入資料
    if args.sample:
        logger.info(f"載入範例資料: {SAMPLE_DATA}")
        data = load_data(SAMPLE_DATA)
    else:
        data_path = Path(args.data)
        if not data_path.exists():
            logger.error(f"找不到資料檔: {data_path}")
            sys.exit(1)
        logger.info(f"載入資料: {data_path}")
        data = load_data(data_path)

    # 2. 補上 generated_at（若資料中未提供）
    if "generated_at" not in data:
        data["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 3. AI 分析
    if args.no_ai or args.sample:
        if args.no_ai:
            data["ai_analysis"] = placeholder_analysis()
        elif "ai_analysis" not in data:
            # sample 模式下若 JSON 已有 ai_analysis 則直接使用
            data["ai_analysis"] = placeholder_analysis()
        logger.info("AI 分析：使用範例資料中的預設內容")
    else:
        prompt = build_analysis_prompt(data)
        data["ai_analysis"] = call_claude_api(prompt)
        logger.info("AI 分析完成")

    # 4. 渲染 HTML
    logger.info("渲染 HTML 模板...")
    html = render_template(data)

    # 5. 儲存
    out_path = save_report(html, data.get("report_date", datetime.now().strftime("%Y-%m-%d")))
    logger.info(f"報表已產生: {out_path}")
    print(f"\n✅ 輸出路徑: {out_path}")


if __name__ == "__main__":
    main()
