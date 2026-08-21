"""oel_limit 限值表 — 判定引擎基准数据 (职业接触限值 OEL)

数据源:
  GBZ 2.1—2019 化学有害因素  (OEL 表: 序号/中文名/英文名/CAS/MAC/PC-TWA/PC-STEL/临界效应/备注)
      ⚠️ 主标准 PDF 缺失 (只有第1号修改单), 待用户行业渠道提供后解析
  GBZ 2.2—2007 物理因素      (规则型: 噪声85dB(A)等效/高温WBGT/手传振动5m/s²/工频电场5kV/m...)
      ✅ 本地扫描件已 OCR (knowledge/ocr_pdf.py), 表格 OCR 质量差需人工核对

设计: 统一表, 化学因素按 物质×限值类型 一行, 物理因素按 因素×规则 一行
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ohs.db"

DDL = """
CREATE TABLE IF NOT EXISTS oel_limit (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  factor_name     TEXT NOT NULL,          -- 中文名 (苯/甲苯/噪声/高温/手传振动...)
  factor_type     TEXT NOT NULL,          -- 化学有害因素 | 粉尘 | 物理因素
  cas             TEXT,                   -- 化学文摘号 (化学因素)
  english_name    TEXT,                   -- 英文名 (化学因素)
  oel_type        TEXT NOT NULL,          -- MAC | PC-TWA | PC-STEL | 超限倍数 | LEX8h |
                                          -- WBGT | 4h等能量 | 场强 | 功率密度 ...
  value           TEXT NOT NULL,          -- 数值 (可为 "3" "50" "85" "5" 或范围)
  unit            TEXT,                   -- mg/m3 | dB(A) | °C | kV/m | mW/cm2 | m/s2
  conditions      TEXT,                   -- 适用条件 (接触时间/体力劳动分级/频率等)
  note            TEXT,                   -- 皮/敏/G1.. 或 临界不良健康效应
  source_standard TEXT NOT NULL,          -- GBZ 2.1—2019 | GBZ 2.2—2007
  verified        INTEGER DEFAULT 0,      -- 0=机器提取待人工核对 1=已人工核对
  UNIQUE(factor_name, oel_type, conditions, source_standard)
);

CREATE INDEX IF NOT EXISTS idx_oel_factor ON oel_limit(factor_name);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(DDL)
    return conn


def insert(conn: sqlite3.Connection, rows: list[dict]):
    """批量插入 (UNIQUE 冲突忽略, 幂等)"""
    conn.executemany("""
        INSERT OR IGNORE INTO oel_limit
        (factor_name, factor_type, cas, english_name, oel_type, value, unit,
         conditions, note, source_standard, verified)
        VALUES (:factor_name, :factor_type, :cas, :english_name, :oel_type,
                :value, :unit, :conditions, :note, :source_standard, :verified)
    """, rows)
    conn.commit()


def summary(conn: sqlite3.Connection) -> dict:
    cur = conn.execute("""
        SELECT source_standard, factor_type, COUNT(*)
        FROM oel_limit GROUP BY source_standard, factor_type
    """)
    return {f"{r[0]} / {r[1]}": r[2] for r in cur}


if __name__ == "__main__":
    conn = connect()
    print("oel_limit 表就绪:", DB_PATH)
    print("当前内容:", summary(conn))
