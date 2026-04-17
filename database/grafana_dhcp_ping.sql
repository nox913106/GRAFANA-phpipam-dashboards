-- ============================================================
-- GRAFANA-phpipam-dashboards - DHCP Ping 延遲監控資料表
-- 
-- 獨立於 DEV-phpipam-health-monitor，供 Grafana 日巡檢使用
-- 需在 phpIPAM MariaDB (TW_IPAM) 中建立
-- ============================================================

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
COMMENT='DHCP Server Ping 延遲歷史 (Grafana 日巡檢用)';
