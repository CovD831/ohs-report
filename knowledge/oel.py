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

from knowledge.paths import DB_PATH, db_uri   # 路径唯一定义在 knowledge/paths.py


class OfficialSchemaMissing(RuntimeError):
    """官方数据层表不存在。具名错误, 为了不让它退化成 `no such table: oel_limit` 那种
    要在栈里找半天才知原因的报错 —— 空 volume 首启 / 挂错目录 都走这里。"""


def connect_ro() -> sqlite3.Connection:
    """只读连接 —— 读路径 (报表取数/校验/验收工具) 一律用它。

    与 connect() 的分工是实测逼出来的: `connect()` 每次都 `executescript(DDL)` 且会
    `mkdir` (见下方 connect), 把建表写在读路径上既慢 (每次导出上百次调用) 又破读写分界,
    还会在错误 cwd 下静默造出空库。只读连接三者都不发生: 缺文件/缺表直接抛错。
    """
    if not DB_PATH.exists():
        raise OfficialSchemaMissing(f"库文件不存在: {DB_PATH} — 先跑 bootstrap (oel.connect/ensure_official_schema)")
    conn = sqlite3.connect(db_uri(read_only=True), uri=True)
    return conn


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


def ensure_official_schema() -> None:
    """官方表存在性的显式前置条件 (启动期调用一次)。

    为什么需要: 26 张官方表的 DDL 只在 `connect()` 里被执行, 而 `web/auth.py` 只建
    users/audit_log, `web/app.py:134` 的 connect() 在写路径里 —— 也就是说今天
    "官方表存在"是各条路径碰巧先跑过一次 connect() 的结果。读门改用 connect_ro() 之后,
    空 volume 首启会直接 no such table; 所以把它变成有人负责的启动步骤。
    """
    conn = connect()
    try:
        conn.execute("SELECT 1 FROM oel_limit LIMIT 1")
    except sqlite3.OperationalError as e:
        raise OfficialSchemaMissing(f"官方数据层schema不完整: {e}") from e
    finally:
        conn.close()


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
