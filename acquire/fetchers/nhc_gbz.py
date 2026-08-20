"""GBZ 标准 — 卫健委卫生标准源 (时效核验)
已实测: std.samr 平台不含 GBZ 系列 (GBZ 为卫健委发布的国家职业卫生标准)
时效核验策略:
  1. 基线: 本地旧系统 gbz_standards.db (988 条元数据+状态快照)
  2. 增量: 卫健委 GBZ 发布公告页 (静态 HTML, crawl4ai/r.jina.ai 可抓, TODO)
  3. 兜底: 卫健委 WAS5 标准搜索被瑞数反爬保护, 需浏览器工具 (browser-use)
输出: acquire/raw/nhc_gbz/gbz_baseline.json
"""
import json
import sqlite3
from pathlib import Path

GBZ_DB = Path.home() / "Desktop/law/data/gbz_standards.db"
OUT_DIR = Path(__file__).resolve().parent.parent / "raw" / "nhc_gbz"


def fetch() -> list[dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(GBZ_DB))
    rows = conn.execute(
        "SELECT standard_number, title, status, publish_date, implement_date FROM gbz_standards"
    ).fetchall()
    data = [
        {"code": r[0], "title": r[1], "status": r[2],
         "publish_date": r[3], "implement_date": r[4]}
        for r in rows
    ]
    (OUT_DIR / "gbz_baseline.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1))
    print(f"GBZ 基线导出 {len(data)} 条 -> raw/nhc_gbz/gbz_baseline.json")
    print("TODO: 卫健委 GBZ 公告增量抓取 (crawl4ai), WAS5 搜索需浏览器兜底")
    return data


if __name__ == "__main__":
    fetch()
