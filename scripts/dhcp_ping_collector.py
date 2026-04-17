#!/usr/bin/env python3
"""
DHCP Server Ping Collector
==========================
定期 Ping 所有 DHCP 伺服器並將結果寫入 MariaDB。
供 Grafana 日巡檢 Dashboard「連線健康度」Section 使用。

部署主機: stwphpipam-p
部署路徑: /opt/tools/grafana-dhcp-ping/
資料表:   grafana_dhcp_ping_history (phpipam DB)

使用方式:
  # 單次執行（適合 cron）
  python3 dhcp_ping_collector.py

  # Daemon 模式（持續執行，每 interval 秒一次）
  python3 dhcp_ping_collector.py --daemon --interval 30

Cron 設定（/etc/crontab）:
  * * * * * root cd /opt/tools/grafana-dhcp-ping && python3 scripts/dhcp_ping_collector.py >> /var/log/grafana-dhcp-ping.log 2>&1
"""

import json
import subprocess
import re
import os
import sys
import time
import argparse
import logging
from datetime import datetime

# ============================================================
# 配置
# ============================================================

# 配置檔路徑（相對於腳本位置）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, '..', 'config', 'dhcp_servers.json')

# MariaDB 連線
# stwphpipam-p 上的 MariaDB 容器 (phpipam_phpipam-mariadb_1) 已映射 3306 到 host
DB_CONFIG = {
    'host': os.environ.get('PHPIPAM_DB_HOST', 'localhost'),
    'port': int(os.environ.get('PHPIPAM_DB_PORT', '3306')),
    'user': os.environ.get('PHPIPAM_DB_USER', 'phpipam'),
    'password': os.environ.get('PHPIPAM_DB_PASSWORD', 'my_secret_phpipam_pass'),
    'database': os.environ.get('PHPIPAM_DB_NAME', 'phpipam'),
}

# 資料保留天數
DATA_RETENTION_DAYS = 7

# Ping 參數
PING_COUNT = 1
PING_TIMEOUT = 2  # 秒

# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================
# 核心功能
# ============================================================

def load_dhcp_servers():
    """載入 DHCP 伺服器配置"""
    config_path = os.path.normpath(CONFIG_PATH)
    if not os.path.exists(config_path):
        logger.error(f"配置檔不存在: {config_path}")
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        servers = json.load(f)

    # 只回傳啟用的伺服器
    enabled = [s for s in servers if s.get('enabled', True)]
    logger.info(f"載入 {len(enabled)}/{len(servers)} 台 DHCP 伺服器")
    return enabled


def ping_host(ip):
    """
    Ping 單一主機，回傳 (reachable, latency_ms)
    stwphpipam-p 為 Linux 主機
    """
    try:
        cmd = ['ping', '-c', str(PING_COUNT), '-W', str(PING_TIMEOUT), ip]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=PING_TIMEOUT + 5
        )

        output = result.stdout + result.stderr

        # 解析延遲: time=1.23 ms 或 time<1 ms
        latency = None
        match = re.search(r'time[=<]([0-9.]+)\s*ms', output, re.IGNORECASE)
        if match:
            latency = float(match.group(1))

        reachable = (result.returncode == 0)
        return reachable, latency

    except subprocess.TimeoutExpired:
        return False, None
    except Exception as e:
        logger.error(f"Ping {ip} 例外: {e}")
        return False, None


def get_db_connection():
    """取得 MariaDB 連線 (phpipam_phpipam-mariadb_1, port 3306 mapped to host)"""
    try:
        import pymysql
        conn = pymysql.connect(
            host=DB_CONFIG['host'],
            port=DB_CONFIG['port'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            database=DB_CONFIG['database'],
            charset='utf8mb4',
            autocommit=True
        )
        return conn
    except ImportError:
        logger.error("需要安裝 pymysql: pip install pymysql")
        sys.exit(1)
    except Exception as e:
        logger.error(f"資料庫連線失敗: {e}")
        sys.exit(1)


def ensure_table_exists(conn):
    """確保資料表存在"""
    create_sql = """
    CREATE TABLE IF NOT EXISTS grafana_dhcp_ping_history (
        id INT AUTO_INCREMENT PRIMARY KEY,
        recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        dhcp_ip VARCHAR(45) NOT NULL COMMENT 'DHCP 伺服器 IP',
        dhcp_hostname VARCHAR(64) COMMENT 'DHCP 伺服器主機名稱',
        location VARCHAR(64) COMMENT '所在位置',
        reachable TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否可達 (1=是, 0=否)',
        latency_ms DECIMAL(10,2) COMMENT '回應延遲 (ms)',
        INDEX idx_recorded_at (recorded_at),
        INDEX idx_dhcp_ip (dhcp_ip),
        INDEX idx_dhcp_ip_recorded (dhcp_ip, recorded_at DESC)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='DHCP Server Ping 延遲歷史 (Grafana 日巡檢用)'
    """
    with conn.cursor() as cursor:
        cursor.execute(create_sql)
    logger.info("資料表 grafana_dhcp_ping_history 已確認存在")


def insert_ping_result(conn, server, reachable, latency_ms):
    """寫入 Ping 結果"""
    sql = """
    INSERT INTO grafana_dhcp_ping_history
        (dhcp_ip, dhcp_hostname, location, reachable, latency_ms)
    VALUES (%s, %s, %s, %s, %s)
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            server['ip'],
            server.get('hostname', ''),
            server.get('location', ''),
            1 if reachable else 0,
            latency_ms
        ))


def cleanup_old_data(conn):
    """清理過期資料"""
    sql = """
    DELETE FROM grafana_dhcp_ping_history
    WHERE recorded_at < NOW() - INTERVAL %s DAY
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (DATA_RETENTION_DAYS,))
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"清理 {deleted} 筆過期資料（>{DATA_RETENTION_DAYS} 天）")


def run_once():
    """單次執行：Ping 所有 DHCP Server 並寫入 DB"""
    servers = load_dhcp_servers()
    conn = get_db_connection()
    ensure_table_exists(conn)

    online = 0
    for server in servers:
        reachable, latency = ping_host(server['ip'])
        insert_ping_result(conn, server, reachable, latency)

        status = f"{latency:.2f}ms" if latency else "UNREACHABLE"
        symbol = "✅" if reachable else "❌"
        logger.info(f"  {symbol} {server['hostname']:20s} ({server['ip']:15s}) → {status}")

        if reachable:
            online += 1

    # 每次執行都嘗試清理舊資料
    cleanup_old_data(conn)

    logger.info(f"完成: {online}/{len(servers)} 台在線")
    conn.close()


def run_daemon(interval):
    """Daemon 模式：持續執行"""
    logger.info(f"=== DHCP Ping Collector Daemon ===")
    logger.info(f"間隔: {interval} 秒")
    logger.info(f"啟動時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"==================================")

    iteration = 0
    while True:
        iteration += 1
        try:
            run_once()
        except Exception as e:
            logger.error(f"第 {iteration} 次迭代失敗: {e}")

        time.sleep(interval)


# ============================================================
# 主程式
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='DHCP Server Ping Collector')
    parser.add_argument('--daemon', action='store_true', help='Daemon 模式持續執行')
    parser.add_argument('--interval', type=int, default=60, help='Daemon 模式間隔秒數 (預設: 60)')
    args = parser.parse_args()

    if args.daemon:
        run_daemon(args.interval)
    else:
        run_once()


if __name__ == '__main__':
    main()
