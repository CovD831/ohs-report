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
sys.path.insert(0, str(ROOT))
MANIFEST = ROOT / "data_stamp.json"
# 库路径走单一产地 knowledge.paths.DB_PATH (2026-10-16 修): 这里原本硬写 ROOT/"data"/"ohs.db",
#   于是 `OHS_DATA_DIR` 搬家时它读的**还是仓库里那份真库**。实测踩出来的: 给临时副本的
#   oel_limit 改一格再跑 --check, 它照样报"一致" —— 差点把这条尺子误判成"看不见内容变化",
#   而真相是它根本没看那棵树。路径收口 (S1a′) 漏了这支, 补上。
from knowledge.paths import DB_PATH as DB  # noqa: E402

# 不参与版本戳的表: 运行时状态与项目业务数据 (生产/本地本就应不同)
RUNTIME_TABLES = {"project", "users", "audit_log", "task_job", "equipment_material",
                  "exposure_capture", "export_cache"}


def official_tables(conn: sqlite3.Connection) -> list[str]:
    rows = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    return sorted(rows - RUNTIME_TABLES)


def table_digest(conn: sqlite3.Connection, t: str) -> dict:
    """表内容指纹: 行数 + 列签名 + 内容摘要

    ⚠ 必须**与插入顺序无关** (2026-10-15 实测): 生产与本地同一张表内容一致,
      但某次重灌让自增 id 全部错位 (gbz1_rule 76 行逐行 rowid 都比不出相同) →
      按 rowid 序哈希会报"内容不同", 版本戳就变成天天狼来了。
      做法: 丢掉代理键 id, 对每行取摘要后再按摘要排序哈希。
    """
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
    keep = [i for i, name in enumerate(cols) if name != "id"]
    kept_cols = [cols[i] for i in keep]
    row_hashes = []
    for row in conn.execute(f"SELECT {','.join(cols)} FROM {t}"):
        payload = repr(tuple(row[i] for i in keep))
        row_hashes.append(hashlib.sha1(payload.encode()).hexdigest())
    body = hashlib.sha1((",".join(sorted(row_hashes))).encode()).hexdigest()[:12]
    return {"rows": len(row_hashes), "cols": "|".join(kept_cols), "sha1": body}


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
