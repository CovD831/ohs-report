#!/usr/bin/env python3
"""官方数据层清洗: 把规则库列里存的废止标准号改写为现行版 (默认 dry-run)

为什么需要 (2026-10-15 版本戳首跑): 生产 emergency_rule/protection_rule 还挂着
  GBZ 2.2—2007 / GB/T 38144.1—2019, 本地 oel_limit.source_standard 也存 GBZ 2.2—2007。
  生成端已有 rows_of 兜底 (读不出废止号), 但**数据本身仍是错的**: 任何绕过 rows_of 的
  读取 (knowledge 侧直读、人工查库、将来新代码) 都会把它带出来。兜底管印什么, 清洗管存什么。

安全边界:
  - 默认 dry-run, 只打印 旧→新; 加 --apply 才写库。
  - **分两组**: A 组=纯依据标注 (management/emergency/protection/ppe_item/standard_ref),
    可改写; B 组=数值行 (oel_limit/bio_limit/exposure_level), **本工具永远拒绝改写** ——
    只把标签刷成现行版等于用新版名义发旧版数值, 比明写旧版更危险; 数值须走复核重入库。
  - 不碰 standard_db (它是判定依据本身, 改它=改尺子)。
  - 只改"库里判为废止且能解析出现行版"的值; 解析不出的一律不动 (不猜), 单列成待人工清单。
  - 写前先备份 data/ohs.db 到 data/ohs.db.bak-<时间戳>, 写后立即复扫确认 A 组残留为 0。

用法:
  python tools/fix_official_std_codes.py             # 预演
  python tools/fix_official_std_codes.py --apply     # 实写 (生产需单独批准)
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from web.standard_version import make_ref_fixer, norm_code  # noqa: E402

# (表, 存标准号的列, 主键列) — 只列"会印进报告的依据列"
TARGETS = [
    ("oel_limit", "source_standard", "id"),
    ("bio_limit", "source_standard", "id"),
    ("exposure_level", "source_standard", "id"),
    ("emergency_rule", "std_code", "id"),
    ("protection_rule", "std_code", "id"),
    ("management_rule", "std_code", "id"),
    ("ppe_item", "std_code", "id"),
    ("standard_ref", "code", "id"),
]
# ⚠ 数值行**永不自动改标签** (2026-10-15 实测拦住一次自伤):
#   oel_limit 有 76 行物理因素限值标注 GBZ 2.2—2007, 且**没有任何 2019 版对应行**
#   → 存进去的数值就是 2007 版的。把标签刷成 GBZ 2.2—2019 等于用现行版的名义
#     发旧版数值, 比现在"明写旧版"更危险 (噪声/高温/紫外/脉冲噪声在 2019 版有调整)。
#   正解是按新版**复核数值后重新入库**, 那是另一件事, 不由本工具顺手做掉。
VALUE_BEARING = {"oel_limit", "bio_limit", "exposure_level"}
APPLY = "--apply" in sys.argv


def table_exists(conn: sqlite3.Connection, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def main() -> int:
    db = ROOT / "data" / "ohs.db"
    conn = sqlite3.connect(str(db))
    fix = make_ref_fixer(conn)
    plan: list[tuple[str, int, str, str, str, str]] = []   # (表, 主键列, 主键, 列, 旧值, 新值)
    # 库里判为废止的号 (归一口径), 用于复查"改不动"的残留
    dead = {norm_code(r[0]) for r in conn.execute(
        "SELECT code FROM standard_db WHERE state IN ('废止','已废止')")}
    for t, col, pk in TARGETS:
        if not table_exists(conn, t):
            print(f"  – 跳过 {t} (表不存在)")
            continue
        for rid, val in conn.execute(f"SELECT {pk}, {col} FROM {t}"):
            if not val or not str(val).strip():
                continue
            new, changes = fix(str(val))
            if changes:
                plan.append((t, pk, rid, col, str(val), new))
    # 判为废止却改写不动的 (库里解析不出现行版) 必须显式列出, 不能静默
    skipped: list[tuple[str, int, str, str]] = []
    planned = {(p[0], p[1], p[2]) for p in plan}
    for t, col, pk in TARGETS:
        if not table_exists(conn, t):
            continue
        for rid, val in conn.execute(f"SELECT {pk}, {col} FROM {t}"):
            if val and norm_code(str(val)) in dead and (t, pk, rid) not in planned:
                skipped.append((t, rid, col, str(val)))

    print(f"=== 官方数据层标准号清洗 ({'APPLY 实写' if APPLY else 'dry-run 预演'}) ===")
    writable = [p for p in plan if p[0] not in VALUE_BEARING]
    refused = [p for p in plan if p[0] in VALUE_BEARING]
    print(f"\n— A 组 纯依据标注, 可改 ({len(writable)} 处):")
    for t, pk, rid, col, old, new in writable:
        # 把整行一起打出来: 判据文字本身可能也抄自旧版 (如"≤10 用过滤式"来自 18664 的
        # APF 分级), 那是**内容**问题不是标签问题 —— 内容对不对要由人看, 工具不代判。
        full = conn.execute(f"SELECT * FROM {t} WHERE {pk}=?", (rid,)).fetchone()
        cols = [d[1] for d in conn.execute(f"PRAGMA table_info({t})")]
        body = " | ".join(f"{n}={str(v)[:56]}" for n, v in zip(cols, full or []) if n != pk)
        # ⚑ 行内带数值判据 (APF/危害因数/≤≥+数字) 的, 数值本身可能抄自旧版表格:
        #    这类行改标签=让旧数值挂新版名, 与 B 组同险 —— 必须人确认内容再动。
        flag = "  ⚑含数值判据, 须确认新版是否同值" if re.search(r"(APF|危害因数|≤|≥|<|>)\s*\d", body) else ""
        print(f"   {t}#{rid} {col}: {old!r} → {new!r}{flag}\n       行内容: {body}")
    if not writable:
        print("   无")
    print(f"\n— B 组 数值行, **本工具拒绝改写** ({len(refused)} 处):")
    for t in sorted({p[0] for p in refused}):
        n = sum(1 for p in refused if p[0] == t)
        olds = sorted({p[4] for p in refused if p[0] == t})
        print(f"   {t}: {n} 行, 现标 {olds} — 存的是旧版**数值**, 只改标签=用现行版名义发旧数据")
        print("     → 须按新版复核限值后重新入库 (另一件事), 本工具不顺手做掉")

    if skipped:
        print(f"\n  ⚠ 判为废止但**无法自动改写** {len(skipped)} 处 (库里解析不出现行版, 需人工定):")
        for t, rid, col, old in skipped[:12]:
            print(f"     {t}#{rid} {col} = {old!r}")

    if not APPLY:
        print(f"\n预演完成: A 组待改 {len(writable)} 处, B 组拒绝 {len(refused)} 处。确认无误后加 --apply 写 A 组。")
        conn.close()
        return 0

    if not writable:
        print("\nA 组为空, 无需写库。")
        conn.close()
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = db.with_suffix(f".db.bak-{stamp}")
    shutil.copy2(db, bak)
    print(f"\n  已备份 → {bak.name}")
    cur = conn.cursor()
    for t, pk, rid, col, _old, new in writable:
        cur.execute(f"UPDATE {t} SET {col}=? WHERE {pk}=?", (new, rid))
    conn.commit()
    left = 0
    for t, col, pk in TARGETS:
        if not table_exists(conn, t) or t in VALUE_BEARING:
            continue                       # B 组本来就不该"清零", 它由复核流程负责
        for (val,) in conn.execute(f"SELECT {col} FROM {t}"):
            if val and fix(str(val))[1]:
                left += 1
    print(f"  已写 A 组 {len(writable)} 处; A 组复扫残留 {left} 处 (应为 0)")
    conn.close()
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
