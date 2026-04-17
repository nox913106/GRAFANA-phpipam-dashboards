#!/usr/bin/env python3
"""
IP Change Detector (靜態 IP 異動偵測器)
=======================================
比對 phpIPAM 中靜態 IP 的關鍵欄位（MAC / Hostname / Owner）與上次快照，
偵測到變更時寫入 grafana_ip_changes 表。

供 Grafana 日巡檢 Dashboard「最近 24 小時 IP 異動」Panel 使用。

部署主機: stwphpipam-p
部署路徑: /opt/tools/grafana-dhcp-ping/

異動類型:
  ADD      - 新增的靜態 IP（快照中不存在）
  MODIFY   - 欄位變更（MAC / Hostname / Owner）
  DELETED  - 已刪除的靜態 IP（快照中存在但 phpIPAM 已無此 IP）

邏輯:
  1. 從 ipaddresses 表讀取所有靜態 IP（排除 DHCP Pool、Dynamic Lease、Gateway）
  2. 與 grafana_ip_snapshot 表比對
  3. 新 IP → 記錄 ADD，已有 IP 欄位變更 → 記錄 MODIFY，快照有但已消失 → 記錄 DELETED
  4. 更新 grafana_ip_snapshot 為最新狀態（刪除的 IP 從快照移除）
  5. 首次執行只建立快照，不產生任何異動紀錄

Cron 設定（/etc/crontab）:
  */5 * * * * root cd /opt/tools/grafana-dhcp-ping && python3 scripts/ip_change_detector.py >> /var/log/grafana-ip-changes.log 2>&1
"""

import os
import sys
import logging
from datetime import datetime

# ============================================================
# 配置
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# MariaDB 連線（與 dhcp_ping_collector.py 相同）
DB_CONFIG = {
    'host': os.environ.get('PHPIPAM_DB_HOST', 'localhost'),
    'port': int(os.environ.get('PHPIPAM_DB_PORT', '3306')),
    'user': os.environ.get('PHPIPAM_DB_USER', 'phpipam'),
    'password': os.environ.get('PHPIPAM_DB_PASSWORD', 'my_secret_phpipam_pass'),
    'database': os.environ.get('PHPIPAM_DB_NAME', 'phpipam'),
}

# 監控的欄位
TRACKED_FIELDS = ['mac', 'hostname', 'owner']

# 異動紀錄保留天數
CHANGE_RETENTION_DAYS = 30

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

def get_db_connection():
    """取得 MariaDB 連線"""
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


def ensure_tables_exist(conn):
    """確保資料表存在"""
    sql_path = os.path.join(SCRIPT_DIR, '..', 'database', 'grafana_ip_changes.sql')
    sql_path = os.path.normpath(sql_path)

    if os.path.exists(sql_path):
        with open(sql_path, 'r', encoding='utf-8') as f:
            sql_content = f.read().replace('\r', '')

        with conn.cursor() as cursor:
            for statement in sql_content.split(';'):
                statement = statement.strip()
                if 'CREATE TABLE' in statement.upper():
                    cursor.execute(statement)
        logger.info("資料表已確認存在")
    else:
        logger.warning(f"SQL 檔案不存在: {sql_path}，使用內建 DDL")
        _create_tables_inline(conn)


def _create_tables_inline(conn):
    """內建建表（備援）"""
    with conn.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS grafana_ip_snapshot (
                ip_addr INT UNSIGNED NOT NULL PRIMARY KEY,
                ipam_id INT UNSIGNED COMMENT 'ipaddresses.id',
                subnet_id INT NOT NULL,
                mac VARCHAR(20),
                hostname VARCHAR(255),
                owner VARCHAR(128),
                state TINYINT,
                ip_display VARCHAR(45),
                subnet_cidr VARCHAR(32),
                subnet_desc VARCHAR(255),
                section_name VARCHAR(128),
                snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_subnet_id (subnet_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS grafana_ip_changes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip_addr INT UNSIGNED NOT NULL,
                ip_display VARCHAR(45),
                subnet_cidr VARCHAR(32),
                subnet_desc VARCHAR(255),
                section_name VARCHAR(128),
                changed_field VARCHAR(32) NOT NULL COMMENT 'ADD / MODIFY / DELETED',
                old_value VARCHAR(255),
                new_value VARCHAR(255),
                changed_by VARCHAR(128) COMMENT '操作帳號',
                INDEX idx_detected_at (detected_at),
                INDEX idx_ip_addr (ip_addr),
                INDEX idx_changed_field (changed_field)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)


def fetch_current_static_ips(conn):
    """
    讀取所有靜態 IP 的關鍵欄位
    排除: DHCP Pool、Dynamic Lease (state=7)、Gateway、TC_YueyuenHotel section
    """
    sql = """
    SELECT
        a.id AS ipam_id,
        a.ip_addr,
        a.subnetId AS subnet_id,
        IFNULL(a.mac, '') AS mac,
        IFNULL(a.hostname, '') AS hostname,
        IFNULL(a.owner, '') AS owner,
        a.state,
        INET_NTOA(a.ip_addr) AS ip_display,
        CONCAT(INET_NTOA(s.subnet), '/', s.mask) AS subnet_cidr,
        IFNULL(s.description, '') AS subnet_desc,
        IFNULL(sec.name, '') AS section_name
    FROM ipaddresses a
    JOIN subnets s ON a.subnetId = s.id
    LEFT JOIN sections sec ON s.sectionId = sec.id
    WHERE a.state IN (1, 2, 3)
      AND IFNULL(a.is_gateway, 0) != 1
      AND IFNULL(a.custom_DHCP_pool_range, 0) != 1
      AND IFNULL(sec.name, '') != 'TC_YueyuenHotel'
    """
    with conn.cursor() as cursor:
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
    results = [dict(zip(columns, row)) for row in rows]
    # pymysql 可能將 INT UNSIGNED 回傳為 str，統一轉為 int
    # MAC 統一轉小寫，避免 phpIPAM 掃描大小寫切換造成假異動
    for r in results:
        r['ip_addr'] = int(r['ip_addr'])
        r['mac'] = (r['mac'] or '').lower()
    return results


def fetch_snapshot(conn):
    """讀取現有快照（含顯示用欄位和 ipam_id，供 DELETED 紀錄使用）"""
    sql = """SELECT ip_addr, mac, hostname, owner, state,
                    ip_display, subnet_cidr, subnet_desc, section_name,
                    ipam_id
             FROM grafana_ip_snapshot"""
    with conn.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()
    result = {}
    for row in rows:
        key = int(row[0])  # 統一型別為 int
        result[key] = {
            'ip_addr': key,
            'mac': row[1] or '', 'hostname': row[2] or '',
            'owner': row[3] or '', 'state': row[4],
            'ip_display': row[5] or '', 'subnet_cidr': row[6] or '',
            'subnet_desc': row[7] or '', 'section_name': row[8] or '',
            'ipam_id': row[9]
        }
    return result


def lookup_changed_by(conn, ipam_id=None, ip_display=None):
    """
    從 phpIPAM changelog + users 表查詢最近操作該 IP 的帳號
    優先用 ipam_id (ipaddresses.id) 查詢；
    若 ipam_id 為 None 或查無結果，改用 ip_display 比對 changelog.cdiff 備援查詢。
    回傳: username 字串，查無則回傳 '(系統掃描)'
    """
    # 方法 1：透過 ipam_id 精確查詢（限 10 分鐘內，避免把舊操作歸到 cron 掃描的變更）
    if ipam_id:
        sql = """
        SELECT u.username
        FROM changelog c
        JOIN users u ON c.cuser = u.id
        WHERE c.ctype = 'ip_addr'
          AND c.coid = %s
          AND c.cdate >= NOW() - INTERVAL 10 MINUTE
        ORDER BY c.cdate DESC
        LIMIT 1
        """
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, (ipam_id,))
                row = cursor.fetchone()
                if row:
                    return row[0]
        except Exception:
            pass

    # 方法 2：透過 IP 位址字串比對 changelog.cdiff（備援）
    if ip_display:
        sql = """
        SELECT u.username
        FROM changelog c
        JOIN users u ON c.cuser = u.id
        WHERE c.ctype = 'ip_addr'
          AND c.cdiff LIKE %s
          AND c.cdate >= NOW() - INTERVAL 24 HOUR
        ORDER BY c.cdate DESC
        LIMIT 1
        """
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, (f'%{ip_display}%',))
                row = cursor.fetchone()
                if row:
                    return row[0]
        except Exception:
            pass

    return '(系統掃描)'


def record_change(conn, ip_info, change_type, old_val, new_val, changed_by='(unknown)'):
    """
    記錄一筆異動
    change_type: ADD / MODIFY / DELETED
    """
    sql = """
    INSERT INTO grafana_ip_changes
        (ip_addr, ip_display, subnet_cidr, subnet_desc, section_name, changed_field, old_value, new_value, changed_by)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            ip_info['ip_addr'],
            ip_info.get('ip_display', ''),
            ip_info.get('subnet_cidr', ''),
            ip_info.get('subnet_desc', ''),
            ip_info.get('section_name', ''),
            change_type,
            old_val if old_val else '(空值)',
            new_val if new_val else '(空值)',
            changed_by
        ))


def upsert_snapshot(conn, ip_info):
    """更新或新增快照（含顯示用欄位和 ipam_id）"""
    sql = """
    INSERT INTO grafana_ip_snapshot
        (ip_addr, ipam_id, subnet_id, mac, hostname, owner, state, ip_display, subnet_cidr, subnet_desc, section_name)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        ipam_id = VALUES(ipam_id),
        subnet_id = VALUES(subnet_id),
        mac = VALUES(mac),
        hostname = VALUES(hostname),
        owner = VALUES(owner),
        state = VALUES(state),
        ip_display = VALUES(ip_display),
        subnet_cidr = VALUES(subnet_cidr),
        subnet_desc = VALUES(subnet_desc),
        section_name = VALUES(section_name)
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (
            ip_info['ip_addr'],
            ip_info.get('ipam_id'),
            ip_info['subnet_id'],
            ip_info['mac'],
            ip_info['hostname'],
            ip_info['owner'],
            ip_info['state'],
            ip_info.get('ip_display', ''),
            ip_info.get('subnet_cidr', ''),
            ip_info.get('subnet_desc', ''),
            ip_info.get('section_name', '')
        ))


def delete_from_snapshot(conn, ip_addr):
    """從快照中移除已刪除的 IP"""
    sql = "DELETE FROM grafana_ip_snapshot WHERE ip_addr = %s"
    with conn.cursor() as cursor:
        cursor.execute(sql, (ip_addr,))


def cleanup_old_changes(conn):
    """清理過期異動紀錄"""
    sql = "DELETE FROM grafana_ip_changes WHERE detected_at < NOW() - INTERVAL %s DAY"
    with conn.cursor() as cursor:
        cursor.execute(sql, (CHANGE_RETENTION_DAYS,))
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"清理 {deleted} 筆過期異動紀錄（>{CHANGE_RETENTION_DAYS} 天）")


def run():
    """主要執行流程"""
    conn = get_db_connection()
    ensure_tables_exist(conn)

    # 1. 讀取當前靜態 IP
    current_ips = fetch_current_static_ips(conn)
    logger.info(f"讀取 {len(current_ips)} 筆靜態 IP")

    # 2. 讀取快照
    snapshot = fetch_snapshot(conn)
    is_first_run = len(snapshot) == 0
    if is_first_run:
        logger.info("首次執行，建立初始快照（不產生異動紀錄）")

    # 3. 比對：ADD + MODIFY
    add_count = 0
    modify_count = 0
    delete_count = 0
    current_ip_keys = set()

    for ip_info in current_ips:
        ip_key = ip_info['ip_addr']
        current_ip_keys.add(ip_key)

        if ip_key in snapshot:
            # 已有快照 → 比對欄位 → MODIFY
            if not is_first_run:
                old = snapshot[ip_key]
                for field in TRACKED_FIELDS:
                    old_val = old.get(field, '')
                    new_val = ip_info.get(field, '')
                    if old_val != new_val:
                        who = lookup_changed_by(conn, ipam_id=ip_info.get('ipam_id'), ip_display=ip_info.get('ip_display'))
                        record_change(conn, ip_info, 'MODIFY', old_val, new_val, who)
                        modify_count += 1
                        logger.info(
                            f"  ⚡ MODIFY {ip_info['ip_display']} [{field}] "
                            f"'{old_val}' → '{new_val}' by {who}"
                        )
        else:
            # 快照中不存在 → ADD
            if not is_first_run:
                summary = f"hostname={ip_info['hostname']}, mac={ip_info['mac']}, owner={ip_info['owner']}"
                who = lookup_changed_by(conn, ipam_id=ip_info.get('ipam_id'), ip_display=ip_info.get('ip_display'))
                record_change(conn, ip_info, 'ADD', '(不存在)', summary, who)
                add_count += 1
                logger.info(f"  ➕ ADD {ip_info['ip_display']} ({summary}) by {who}")

        # 更新快照
        upsert_snapshot(conn, ip_info)

    # 4. 反向比對：DELETED（快照有但 phpIPAM 已無）
    if not is_first_run:
        for ip_key, old_info in snapshot.items():
            if ip_key not in current_ip_keys:
                summary = f"hostname={old_info['hostname']}, mac={old_info['mac']}, owner={old_info['owner']}"
                who = lookup_changed_by(conn, ipam_id=old_info.get('ipam_id'), ip_display=old_info.get('ip_display'))
                record_change(conn, old_info, 'DELETED', summary, '(已刪除)', who)
                delete_from_snapshot(conn, ip_key)
                delete_count += 1
                logger.info(f"  🗑️ DELETED {old_info['ip_display']} ({summary}) by {who}")

    # 5. 清理舊紀錄
    cleanup_old_changes(conn)

    # 6. 摘要
    if is_first_run:
        logger.info(f"初始快照已建立: {len(current_ips)} 筆 IP")
    else:
        logger.info(f"偵測完成: ADD={add_count}, MODIFY={modify_count}, DELETED={delete_count}")

    conn.close()


# ============================================================
# 主程式
# ============================================================

if __name__ == '__main__':
    run()
