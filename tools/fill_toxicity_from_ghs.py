"""补齐 hazard_toxicity — 用 ghs_class 的 CIS 精确匹配扩展收录

原则(用户红线):
  ✅ 物质身份必须**确定** → 只允许 **CAS 精确匹配** (1:1), 不做任何名称模糊/LIKE。
     (实测: LIKE 匹配把「乙酸」匹到「碘乙酸」、「乙二醇」匹到「2-丁氧基乙醇」→ 张冠李戴)
  ✅ 只收录**二元事实**: 剧毒(is_highly_toxic) / 致癌(is_carcinogen) —— 来自官方目录明文
  ❌ 不推算 THI 危害程度 (数据覆盖不足, 系统性虚高; 见 tools/load_ghs_class.py 文档)
  ❌ 名称匹配不上 → 保持「待补充」, 不猜

新收录条目的 level 取值规则:
  - ghs 有致癌性分类 → level = "致癌"  (标 carcinogen, 非轻/中/高/极度)
  - ghs 标记剧毒      → level = "剧毒"
  - 其余              → 不新增(避免用不安全的数据冒充危害程度)

CLI:
  python tools/fill_toxicity_from_ghs.py           # 预演
  python tools/fill_toxicity_from_ghs.py --apply   # 写入
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "ohs.db"

CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")   # 严格 CAS 格式


def clean_cas(raw: str | None) -> str | None:
    """从 oel_limit.cas 脏值中提取纯 CAS (如 '630-08-0 — — — —' → '630-08-0')"""
    if not raw:
        return None
    m = re.search(r"\b(\d{2,7}-\d{2}-\d)\b", raw)
    return m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    conn = sqlite3.connect(DB)

    # ghs_class 建 CAS 索引
    ghs: dict[str, tuple] = {}
    dup = 0
    for name, cas, tox, carc in conn.execute(
        "SELECT name, cas, is_highly_toxic, is_carcinogen FROM ghs_class WHERE cas != ''"
    ):
        cas = (cas or "").strip()
        if not CAS_RE.match(cas):
            continue
        if cas in ghs:
            dup += 1          # 同 CAS 多条目(如 盐酸/氯化氢), 取致癌或剧毒的并集
            n0, c0, t0 = ghs[cas]
            ghs[cas] = (n0, max(c0, carc), max(t0, tox))
        else:
            ghs[cas] = (name, carc, tox)
    print(f"ghs_class 可用 CAS: {len(ghs)} (同 CAS 合并 {dup})")

    # 现有 hazard_toxicity
    existing = {r[0] for r in conn.execute("SELECT factor_name FROM hazard_toxicity")}
    print(f"hazard_toxicity 现有: {len(existing)} 条")

    # 项目里实际出现的化学因素(5.2-1 候选来源: assess.hazards 由 entity_link 生成)
    allfactors: set[str] = set()
    for name, in conn.execute("SELECT factor_name FROM oel_limit"):
        if name:
            allfactors.add(name.strip())
    for name, in conn.execute("SELECT factor FROM health_effect"):
        if name:
            allfactors.add(name.strip())
    for name, in conn.execute("SELECT name FROM hazard_factor"):
        if name:
            allfactors.add(name.strip())
    for name, in conn.execute("SELECT name FROM hazchem_item"):
        if name:
            allfactors.add(name.strip())
    print(f"候选因素池(oel∪health_effect∪hazard_factor∪hazchem): {len(allfactors)}")

    # CAS 精确匹配
    new_rows = []
    for fac in sorted(allfactors):
        if fac in existing:
            continue
        oel = conn.execute(
            "SELECT cas FROM oel_limit WHERE factor_name=? LIMIT 1", (fac,)
        ).fetchone()
        cas = clean_cas(oel[0] if oel else None)
        if not cas or cas not in ghs:
            continue
        gname, carc, tox = ghs[cas]
        if carc:
            level, note = "致癌", f"GHS致癌性分类(经CAS={cas}精确匹配 {gname})"
        elif tox:
            level, note = "剧毒", f"危化品目录剧毒(经CAS={cas}精确匹配 {gname})"
        else:
            continue          # 无二元事实 → 不新增
        new_rows.append((fac, "", "", level, note))

    print(f"\n可新增(CAS精确 + 有二元事实): {len(new_rows)} 条")
    for fac, _a, _f, lv, note in new_rows[:40]:
        print(f"  {fac:<18} → {lv:<4} ({note})")

    if not a.apply:
        print("\n[预演] 加 --apply 写入。")
        return 0

    for fac, aliases, form, level, note in new_rows:
        conn.execute(
            "INSERT INTO hazard_toxicity(factor_name,aliases,form,level,grade,basis,source,verified) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (fac, aliases, form, level, "",
             f"GBZ/T 230—2025 体系; {note}",
             "mem.gov.cn:危化品分类信息表", 0),
        )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM hazard_toxicity").fetchone()[0]
    print(f"\n✓ hazard_toxicity: {len(existing)} → {total}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
