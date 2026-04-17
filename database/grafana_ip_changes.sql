-- ============================================================
-- GRAFANA-phpipam-dashboards - IP 異動偵測資料表
--
-- 用於偵測靜態 IP 的異動（ADD / MODIFY / DELETED）
-- 需在 phpIPAM MariaDB (TW_IPAM) 中建立
-- ============================================================

-- 快照表：儲存每個靜態 IP 的上一次已知狀態
CREATE TABLE IF NOT EXISTS grafana_ip_snapshot (
    ip_addr INT UNSIGNED NOT NULL COMMENT 'IP 位址 (整數)',
    ipam_id INT UNSIGNED COMMENT 'ipaddresses 表的 id (供 changelog 查詢)',
    subnet_id INT NOT NULL COMMENT '子網路 ID',
    mac VARCHAR(20) COMMENT 'MAC 位址',
    hostname VARCHAR(255) COMMENT '主機名稱',
    owner VARCHAR(128) COMMENT '擁有者',
    state TINYINT COMMENT 'IP 狀態',
    ip_display VARCHAR(45) COMMENT 'IP 位址 (可讀格式)',
    subnet_cidr VARCHAR(32) COMMENT '子網路 CIDR',
    subnet_desc VARCHAR(255) COMMENT '子網路描述',
    section_name VARCHAR(128) COMMENT 'Section 名稱',
    snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (ip_addr),
    INDEX idx_subnet_id (subnet_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='靜態 IP 快照 (Grafana 異動偵測用)';

-- 異動紀錄表：記錄偵測到的 ADD / MODIFY / DELETED
CREATE TABLE IF NOT EXISTS grafana_ip_changes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    ip_addr INT UNSIGNED NOT NULL COMMENT 'IP 位址 (整數)',
    ip_display VARCHAR(45) COMMENT 'IP 位址 (可讀格式)',
    subnet_cidr VARCHAR(32) COMMENT '子網路 CIDR',
    subnet_desc VARCHAR(255) COMMENT '子網路描述',
    section_name VARCHAR(128) COMMENT 'Section 名稱',

    -- 變更類型與內容
    changed_field VARCHAR(32) NOT NULL COMMENT '異動類型: ADD / MODIFY / DELETED',
    old_value VARCHAR(255) COMMENT '舊值',
    new_value VARCHAR(255) COMMENT '新值',
    changed_by VARCHAR(128) COMMENT '操作帳號 (來自 phpIPAM changelog)',

    INDEX idx_detected_at (detected_at),
    INDEX idx_ip_addr (ip_addr),
    INDEX idx_changed_field (changed_field)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='靜態 IP 異動紀錄 (Grafana 日巡檢用)';
