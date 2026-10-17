"""按 GBZ/T 230—2025 维护 hazard_toxicity (危害程度) — 补写 / 重算两种动作, 默认预演

2026-10-14 背景:
  「健康影响表·危害程度」列 10 处待补充, 三向核验结论:
    - 项目材料不提供 (材料缺口);
    - 真稿有值但**真稿是同公司另一产线** (丙烯酸酯) 的健康影响表, 不是本项目
      (不饱和聚酯树脂) 的直接对标 → 不能照抄;
    - 官方数据层可算: GBZ/T 230—2025 PDF 在本地 + ghs_class 2832 条。

纪律:
  - CAS 精确匹配 (禁止无约束子串; skill 教训: 丙酮曾误配丙酮氰醇→剧毒)
  - 补写只动 hazard_toxicity 里**没有**的物质 (不覆盖 verified=1 行, 不覆盖真稿取证行),
    且新行仍受 `MIN_HAVE` 严格门槛约束 (实测<4 项 → 不存, 维持待补充)。
  - 每条带 basis (算法+实测项数+缺项清单) + source (gbz_t_230_2025_thi)
  - 预演默认, 而且**预演连的是只读连接**: 只有 --apply 才走写侧工厂 connect()。
    (S1c′ 读写分家: 一条"看看要改什么"的命令不该给库开可写连接, 更不该顺手跑 DDL。)
  - **口径变了要能把存值重新导出**: `--refresh-thi` 只按 source 前缀认"我们算的"那些行
    (范围从数据派生, 不抄物质名单), 用**报告侧同一把尺子** (min_have=0 + `_cas_from_oel`)
    重算 level/grade/basis/source。
    为什么需要它: 2026-10-16 主口径从"下界"翻成"注6"之后, 按旧口径写入的行就成了
    第二个口径的化石 (实测: 苯醌 存 轻度危害/grade1 = 下界 29, 而注6 是 极度危害/grade4 = 77)。
    人手写一条 UPDATE 迁过去 = 下次口径再变又会留一份化石。
    两个门槛各有用途, 不矛盾: 入库门槛 (严) 管"要不要存", 重算门槛 (与报告一致) 管"存的话存什么"。
  - 写完之后**读回逐行对表** (level/grade 与算出来的一致, 不一致 rc=1) —— commit 没抛异常
    不等于库里就是那个值 (记忆: 写库工具必须读回验形状)。

用法:
  .venv/bin/python tools/backfill_hazard_toxicity.py                 # 预演: 缺什么
  .venv/bin/python tools/backfill_hazard_toxicity.py --apply         # 补写缺失行
  .venv/bin/python tools/backfill_hazard_toxicity.py --refresh-thi   # 预演: 哪些存值落后于当前口径
  .venv/bin/python tools/backfill_hazard_toxicity.py --refresh-thi --apply
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledge.oel import connect, connect_ro   # noqa: E402  写侧/只读工厂: 路径取自 knowledge/paths 单一产地
from web.section_filler import _cas_from_oel   # noqa: E402  CAS 只认 oel_limit 登记, 与报告侧同源
from web.thi_rating import rate_substance      # noqa: E402

# 待补物质 (factor_name, CAS) — 来自最终版报告 t33 健康影响表 10 处 + 相邻物质
# CAS 全部来自 oel_limit 表 (GBZ 2.1—2019) 的权威登记, 不手填
TARGETS = [
    ("N,N-二甲基苯胺", "121-69-7"),
    ("丙酮", "67-64-1"),
    ("乙二醇", "107-21-1"),
    ("乙酸乙酯", "141-78-6"),
    ("二聚环戊二烯", "77-73-6"),
    ("双酚A", "80-05-7"),
    ("对苯二甲酸", "100-21-0"),
    ("苯醌", "106-51-4"),
    ("邻苯二甲酸酐", "85-44-9"),
    ("马来酸酐", "108-31-6"),
    ("二甲苯", "1330-20-7"),
]

# "我们算的"那批行的身份 = source 前缀, 与 web/section_filler.lookup_toxicity 里
# 判断"是否下探注6"用的是同一个字符串 (它认 'thi', 这里认它写入时的那个前缀)。
THI_SOURCE_PREFIX = "gbz_t_230_2025_thi"


def _compute(conn, name: str) -> dict | None:
    """报告侧口径: CAS 取自 oel_limit, min_have=0 (数据不足也要出注6 等级)"""
    return rate_substance(conn, name, _cas_from_oel(conn, name), min_have=0)


def refresh_thi(conn, apply: bool) -> tuple[int, int]:
    """把 source 前缀命中"我们算的"的行按当前口径重算 → (待重算行数, 读回不一致行数)"""
    rows = conn.execute(
        "SELECT id, factor_name, level, grade, source FROM hazard_toxicity "
        "WHERE source LIKE ? ORDER BY id", (THI_SOURCE_PREFIX + "%",)).fetchall()
    print(f"=== --refresh-thi: source 以 {THI_SOURCE_PREFIX} 开头的行 {len(rows)} 条 ===")
    stale = []
    for r in rows:
        res = _compute(conn, r["factor_name"])
        if res is None:
            print(f"[算不出] {r['factor_name']:<12} 现值 {r['level']}/{r['grade']} "
                  f"→ 不动 (GHS/理化数据不足; 报告侧会退回存值)")
            continue
        if res["level"] == r["level"] and res["grade"] == r["grade"]:
            print(f"[一致]   {r['factor_name']:<12} {r['level']}/{r['grade']} "
                  f"(注6 THI={res['thi']}, 实测{res['have']}/8)")
            continue
        print(f"[落后]   {r['factor_name']:<12} 存值 {r['level']}/{r['grade']} "
              f"→ 当前口径 {res['level']}/{res['grade']} (注6 THI={res['thi']}, "
              f"实测{res['have']}/8; 参照下界 {res['thi_zero']}→{res['level_zero']})")
        stale.append((r["id"], r["factor_name"], res))
    if not stale:
        print("\n没有需要重算的行 (存值已与当前口径一致)")
        return 0, 0
    if not apply:
        print(f"\n(预演模式 — 未写库; {len(stale)} 行待重算, 加 --apply 才写入)")
        return len(stale), 0
    for rid, _name, res in stale:
        conn.execute("UPDATE hazard_toxicity SET level=?, grade=?, basis=?, source=? "
                     "WHERE id=?",
                     (res["level"], res["grade"], res["basis"], res["source"], rid))
    conn.commit()
    # 读回对表: 写动作成功与否只能由"再读一次"证明
    back = {r["id"]: (r["level"], r["grade"]) for r in conn.execute(
        "SELECT id, level, grade FROM hazard_toxicity WHERE source LIKE ?",
        (THI_SOURCE_PREFIX + "%",))}
    bad = [(name, want, back.get(rid))
           for rid, name, res in stale
           for want in [(res["level"], res["grade"])] if back.get(rid) != want]
    if bad:
        print(f"\n❌ 读回对表不一致 {len(bad)} 行: {bad}")
        return len(stale), len(bad)
    print(f"\n✅ 已按当前口径重算 {len(stale)} 行并读回核对")
    return len(stale), 0


def main() -> int:
    apply = "--apply" in sys.argv
    # 预演 = 只读 (不建表、不给验收库开可写连接); --apply 才走写侧工厂
    conn = connect() if apply else connect_ro()
    conn.row_factory = sqlite3.Row

    if "--refresh-thi" in sys.argv:
        _n_stale, n_bad = refresh_thi(conn, apply)
        conn.close()
        return 1 if n_bad else 0

    have_rows = {r["factor_name"] for r in
                 conn.execute("SELECT factor_name FROM hazard_toxicity")}
    print(f"=== hazard_toxicity 现有 {len(have_rows)} 条 ===")

    n_new = n_skip = n_none = 0
    plan = []
    for name, cas in TARGETS:
        if name in have_rows:
            r = conn.execute("SELECT level, grade, source FROM hazard_toxicity "
                             "WHERE factor_name=?", (name,)).fetchone()
            print(f"[跳过] {name:<14} 已收录 level={r['level']}/{r['grade']} src={r['source']}")
            n_skip += 1
            continue
        res = rate_substance(conn, name, cas)      # 新行受 MIN_HAVE 严格门槛约束
        if not res:
            print(f"[缺数] {name:<14} GHS/理化数据不足 → 不写 (维持待补充)")
            n_none += 1
            continue
        plan.append((name, cas, res))
        # 主值 = 注6 (与 res['thi_note6'] 恒等); 下界只是复核参照, 不入库、不印出
        print(f"[待写] {name:<14} THI={res['thi']:<5} {res['level']} "
              f"(注6 主口径, 实测{res['have']}/8; 参照下界{res['thi_zero']}→{res['level_zero']}) "
              f"match={res['matched']}/{res['matched_name']}")
        n_new += 1

    print(f"\n=== 汇总: 待写 {n_new} / 已收录跳过 {n_skip} / 数据不足 {n_none} ===")
    if not apply:
        print("(预演模式 — 未写库; 加 --apply 才写入)")
        conn.close()
        return 0

    for name, _cas, res in plan:
        conn.execute(
            "INSERT INTO hazard_toxicity (factor_name, aliases, form, level, grade, "
            "basis, source, verified) VALUES (?,?,?,?,?,?,?,?)",
            (name, "", "", res["level"], res["grade"], res["basis"], res["source"], 0))
    conn.commit()
    back = {r["factor_name"]: (r["level"], r["grade"]) for r in conn.execute(
        "SELECT factor_name, level, grade FROM hazard_toxicity "
        "WHERE source LIKE ? ORDER BY id", (THI_SOURCE_PREFIX + "%",))}
    bad = [(name, (res["level"], res["grade"]), back.get(name))
           for name, _cas, res in plan if back.get(name) != (res["level"], res["grade"])]
    if bad:
        print(f"\n❌ 读回对表不一致 {len(bad)} 行: {bad}")
        conn.close()
        return 1
    print(f"\n✅ 已写入 {len(plan)} 条并读回核对 (现共 {len(back)} 条 {THI_SOURCE_PREFIX} 行)")
    for r in conn.execute("SELECT factor_name, level, grade, source FROM hazard_toxicity "
                          "WHERE source LIKE ? ORDER BY id", (THI_SOURCE_PREFIX + "%",)):
        print("   ", dict(r))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
