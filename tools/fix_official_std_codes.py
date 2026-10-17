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
  - C 组=引用标准的新版**有实质内容变化**(联网核实, 见 CONTENT_CHANGED)或行内含具体表号/数值判据
    的行: 换标签会让旧判据挂新版名, 一律拒绝, 只能连内容一起改。
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

import knowledge.standard_version
from knowledge.paths import DB_PATH  # noqa: E402  (库路径单一产地; 本脚本按设计写库, 保持可写连接)
from knowledge.standard_version import make_ref_fixer, norm_code  # noqa: E402

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
# 人工联网核实过"新版有**实质内容**变化"的标准 → 禁止只换标签 (2026-10-15):
#   GB 18664-2025《呼吸防护装备的选择、使用和维护》发布 2025-08-29, 实施 2026-09-01,
#   强制性 (原 GB/T 18664-2002 是推荐性), 全部代替之。核实到的内容级差异:
#     - 术语 "呼吸防护用品" → "呼吸防护装备"
#     - 缺氧判定阈值 氧含量 <18% → <19.5%
#     - 密合型面罩适合性检验: 旧"首次使用应检验" → 新"每年至少一次"
#     - 新增"判废与更换"专章、IDLH 双人值守、书面呼吸保护计划
#     - APF 数值表 (半面罩10/全面罩100/正压头罩≥1000) 未获一手确认, 二级来源自相矛盾
#   → 引用它的行必须**连内容一起复核**, 只把 std_code 换成 2025 反而更错。
CONTENT_CHANGED = {
    "GB 18664-2025": "新版转强制+缺氧19.5%+适合性检验改每年至少一次+新增判废章; APF 数值表未获一手确认",
    "GBZ2.2-2019": "行内引用具体表号时须核对: '表8' 是 GBZ 2.2—2007 的编号, 2019 版表序不同",
}
# 行内含"版本特定引用"(具体表号/附录/数值判据) → 换标签等于让旧判据挂新版名
_VERSION_SPECIFIC = re.compile(r"(表\s*\d|附录\s*[A-Z]|APF\s*[=≥>]*\s*\d|危害因数\s*[≤≥><]\s*\d|≤\s*\d|≥\s*\d|\d+(?:~\d+)?\s*%)")
APPLY = "--apply" in sys.argv


def table_exists(conn: sqlite3.Connection, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def main() -> int:
    db = DB_PATH
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

    def row_body(t, pk, rid):
        full = conn.execute(f"SELECT * FROM {t} WHERE {pk}=?", (rid,)).fetchone()
        cols = [d[1] for d in conn.execute(f"PRAGMA table_info({t})")]
        return " | ".join(f"{n}={v}" for n, v in zip(cols, full or [])
                          if n not in (pk, "source", "verified"))

    def why_not_safe(t, pk, rid, new):
        """判这行能不能只换标签: 新版有实质内容变化 / 行内含版本特定引用 → 都不能"""
        for key, why in CONTENT_CHANGED.items():
            if norm_code(key) in norm_code(new):
                return f"新版内容有实质变化 → {why}"
        body = row_body(t, pk, rid)
        m = _VERSION_SPECIFIC.search(body)
        if m:
            return f"行内含版本特定引用 ({m.group(0)!r}), 换标签=让旧判据挂新版名"
        return None

    refused = [p for p in plan if p[0] in VALUE_BEARING]
    rest = [p for p in plan if p[0] not in VALUE_BEARING]
    content_review = [p for p in rest if why_not_safe(p[0], p[1], p[2], p[5])]
    writable = [p for p in rest if p not in content_review]

    print(f"\n— A 组 可安全改标签 ({len(writable)} 处):")
    for t, pk, rid, col, old, new in writable:
        print(f"   {t}#{rid} {col}: {old!r} → {new!r}\n       行内容: {row_body(t, pk, rid)[:200]}")
    if not writable:
        print("   无")
    print(f"\n— C 组 **禁止只改标签**, 须连内容一起复核 ({len(content_review)} 处):")
    for t, pk, rid, col, old, new in content_review:
        print(f"   {t}#{rid} {col}: {old!r} → {new!r}")
        print(f"       原因: {why_not_safe(t, pk, rid, new)}")
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
        print(f"\n预演完成: A 组待改 {len(writable)} 处, C 组内容复核 {len(content_review)} 处, B 组数值行拒绝 {len(refused)} 处。只有 A 组会被 --apply 写库。")
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
    knowledge.standard_version.invalidate_caches()    # V3: 同一连接写完不自见, 判据缓存必须显式作废

    # 复扫只针对 A 组范围: C 组(内容复核) 与 B 组(数值行) 是**刻意保留**的,
    # 把它们算进"残留"会把正确的拒绝报成失败 (首跑实测就是 7 处误报)
    left = 0
    for t, pk, rid, col, _old, _new in writable:
        val = conn.execute(f"SELECT {col} FROM {t} WHERE {pk}=?", (rid,)).fetchone()[0]
        if val and fix(str(val))[1]:
            left += 1
            print(f"     ✗ {t}#{rid} 仍是 {val!r}")
    print(f"  已写 A 组 {len(writable)} 处; A 组复扫残留 {left} 处 (应为 0; "
          f"C 组 {len(content_review)} 处与 B 组 {len(refused)} 处按设计保留)")
    conn.close()
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
