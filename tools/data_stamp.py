#!/usr/bin/env python3
"""官方数据层版本戳 — 让"本地测过"与"生产生效"可比

为什么需要 (2026-10-14 实测): quick_deploy 的 md5 清单只证明**代码**进了容器,
  而部署刻意排除 data/ → 官方数据层(GHS 名录/限值/标准库/毒理表)没有任何一致性凭据。
  当天就撞到两起:
    - 本地 hazard_toxicity 104 行 / 生产 103 行 (差一行"苯醌"), 守卫测试在生产直接崩;
    - 更早: 服务器缺 region_facts.json, 接线代码全对但静默空转。

口径: 除运行时/业务表白名单外, **所有表**都参与指纹 (行数 + 列签名 + 内容 sha1)。
  宁可多报漂移, 也不要漏报 —— 出现新表/业务表混入时, 比对会提示, 由人决定是否进白名单。

用法:
  python tools/data_stamp.py                 # 打印当前库指纹 (JSON)
  python tools/data_stamp.py --write         # 写仓库根 data_stamp.json (随源码上送)
  python tools/data_stamp.py --check [路径]  # 实测 vs manifest: 漂移打印明细, exit 1
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data_stamp.json"
DB = ROOT / "data" / "ohs.db"

# 不参与版本戳的表: 运行时状态与项目业务数据 (生产/本地本就应不同)
RUNTIME_TABLES = {"project", "users", "audit_log", "task_job", "equipment_material",
                  "exposure_capture", "export_cache"}


def official_tables(conn: sqlite3.Connection) -> list[str]:
    rows = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    return sorted(rows - RUNTIME_TABLES)


def table_digest(conn: sqlite3.Connection, t: str) -> dict:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
    h = hashlib.sha1((",".join(cols)).encode())
    n = 0
    for row in conn.execute(f"SELECT * FROM {t} ORDER BY rowid"):
        n += 1
        h.update(repr(row).encode())
    return {"rows": n, "cols": "|".join(cols), "sha1": h.hexdigest()[:12]}


def current_stamp(db_path: Path = DB) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = {t: table_digest(conn, t) for t in official_tables(conn)}
    finally:
        conn.close()
    agg = hashlib.sha1(json.dumps(tables, sort_keys=True).encode())
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db": str(db_path),
        "overall": agg.hexdigest()[:12],
        "excluded": sorted(RUNTIME_TABLES),
        "tables": tables,
    }


def diff(manifest: dict, actual: dict) -> list[str]:
    out = []
    mt, at = manifest.get("tables", {}), actual["tables"]
    for t in sorted(set(mt) | set(at)):
        a, b = mt.get(t), at.get(t)
        if a is None:
            out.append(f"  + 新增表 {t}: {b['rows']} 行 (本地/生产其中一侧没有)")
        elif b is None:
            out.append(f"  - 缺失表 {t}: manifest 有 {a['rows']} 行, 实测没有")
        elif a["sha1"] != b["sha1"]:
            kind = "行数不同" if a["rows"] != b["rows"] else \
                   ("列结构不同" if a["cols"] != b["cols"] else "内容不同(行数一致)")
            out.append(f"  ! {t} {kind}: manifest {a['rows']}行/{a['sha1']} "
                       f"vs 实测 {b['rows']}行/{b['sha1']}")
    return out


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "print"
    if mode == "--write":
        stamp = current_stamp()
        MANIFEST.write_text(json.dumps(stamp, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"✓ 已写 {MANIFEST.name}: {len(stamp['tables'])} 表, overall={stamp['overall']}")
        return 0
    if mode == "--check":
        path = Path(sys.argv[2]) if len(sys.argv) > 2 else MANIFEST
        if not path.exists():
            print(f"⚠ 无 manifest {path} — 跳过比对 (先在本机跑 --write)")
            return 0
        manifest = json.loads(path.read_text(encoding="utf-8"))
        d = diff(manifest, current_stamp())
        if d:
            print(f"⚠ 官方数据层与 manifest 有 {len(d)} 处漂移 "
                  f"(manifest 生成于 {manifest.get('generated_at')}):")
            for line in d:
                print(line)
            return 1
        print(f"✓ 官方数据层与 manifest 一致 ({len(manifest['tables'])} 表)")
        return 0
    print(json.dumps(current_stamp(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
