# Grafana phpIPAM 巡檢 Dashboard

phpIPAM 日巡檢、週巡檢、月巡檢的 Grafana Dashboard 定義檔。

## 📁 檔案說明

| 檔案 | 用途 | 時間範圍 |
|------|------|---------| 
| `daily_inspection.json` | 日巡檢 Dashboard | 最近 24 小時 |
| `weekly_inspection.json` | 週巡檢 Dashboard | 最近 7 天 |
| `monthly_inspection.json` | 月巡檢 Dashboard | 最近 30 天 |

## 🔧 匯入方式

1. 登入 Grafana Web UI
2. 點選左側選單 → **Dashboards** → **New** → **Import**
3. 上傳 JSON 檔案或貼上 JSON 內容
4. 選擇 Data Source: **TW_IPAM**
5. 點選 **Import**

## 📊 Dashboard 內容

### 日巡檢 Dashboard
| Panel | 類型 | 說明 |
|-------|------|------|
| 總 IP 數量 | Stat | 所有 IP 總數 |
| Active IP | Stat | state=2，排除 DHCP Pool |
| Offline IP | Stat | state=1，排除 DHCP Pool |
| Reserved IP | Stat | state=3 |
| DHCP Pool IP | Stat | custom_DHCP_pool_range=1 |
| DHCP Lease IP | Stat | state=7 (Dynamic Lease) |
| IP 狀態分佈 | Pie Chart | 甜甜圈圖（排除 DHCP Pool） |
| 各 Section IP 數量 | Pie Chart | 依 Section 分組 |
| 靜態 IP 欄位變更 | Table | 快照比對 MAC/Hostname/Owner 實際變更 |
| 高使用率 TOP 20 | Bar Gauge | 使用率 > 80% 的網段 |
| 高使用率明細 | Table | 含 Used/Total/Free 欄位 |

### 週巡檢 Dashboard
| Panel | 類型 | 說明 |
|-------|------|------|
| 低容量網段數 | Stat | Free IP < 10% 的網段數 |
| 不合規 IP 總數 | Stat | Active 但缺 Hostname/Description |
| 本週異動數 | Stat | 7 天內 editDate 變更的 IP |
| 總監控網段數 | Stat | 排除 Folder 和 /31 /32 |
| 低容量網段明細 | Table | Free % < 10% 的網段清單 |
| 不合規 IP 分佈 | Bar Chart | 按網段分組 TOP 20 |
| 不合規 IP 詳細清單 | Table | IP/MAC/Owner/Last Seen |
| 過去 7 天異動紀錄 | Table | 完整異動明細 |

### 月巡檢 Dashboard
| Panel | 類型 | 說明 |
|-------|------|------|
| 離線 > 180 天設備數 | Stat | 長期離線警告 |
| 離線 > 90 天設備數 | Stat | 中期離線警告 |
| 本月異動數 | Stat | 30 天內變更的 IP |
| 整體 IP 使用率 | Stat | 全局使用百分比 |
| 離線 > 180 天明細 | Table | IP/MAC/Hostname/離線天數 |
| 各 Section 容量統計 | Table | Subnets/Used/Total/Free/Usage% |
| 各 Section IP 使用率 | Pie Chart | 甜甜圈圖 |
| 各 Section IP 狀態分佈 | Bar Chart | Stacked bar (Used/Offline/Reserved) |

## ⚙️ 前置需求

- Grafana Data Source `TW_IPAM` 已連接 phpIPAM MariaDB
- phpIPAM `ipaddresses` 表有 `custom_DHCP_pool_range` 欄位
- `grafana_ip_snapshot` + `grafana_ip_changes` 表已建立（見下方部署步驟）
- Grafana 使用者有 `SELECT` 權限

### IP 異動偵測 Cron（`/etc/crontab`）

```bash
# 每 5 分鐘偵測靜態 IP 欄位異動 (MAC/Hostname/Owner)
*/5 * * * * root cd /opt/tools/grafana-ip-changes && python3 scripts/ip_change_detector.py >> /var/log/grafana-ip-changes.log 2>&1
```

## 🔑 關鍵 SQL 邏輯

### DHCP Pool 排除
```sql
WHERE (custom_DHCP_pool_range IS NULL OR custom_DHCP_pool_range != 1)
```

### 不合規 IP 條件
```sql
WHERE state = 2                    -- Active
  AND is_gateway != 1              -- 非 Gateway
  AND custom_DHCP_pool_range != 1  -- 非 DHCP Pool
  AND hostname IS NULL OR = ''     -- Hostname 為空
  AND description IS NULL OR = '' OR = '-- autodiscovered --'
```

### IP 狀態對照（ipTags）
| state | 名稱 | 說明 |
|-------|------|------|
| 1 | Offline | 離線 |
| 2 | Used | 使用中（Active） |
| 3 | Reserved | 保留 |
| 4 | DHCP pool range | DHCP 範圍 |
| 7 | Dynamic Lease | DHCP 動態租約 |
| 8 | Static reservations | 靜態保留 |
