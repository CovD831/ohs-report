"""GBZ/T 230—2025 毒物危害指数 (THI) 计算 — 官方标准驱动, 无数据不编造

背景 (2026-10-14):
  报告「健康影响表·危害程度」列曾大面积「待补充」。归因三向核验发现:
    - 真稿同列有值 (14/14), 但**真稿是同一公司的另一条产品线** (丙烯酸酯),
      不是本项目 (不饱和聚酯树脂) 的直接对标 → 不能照抄真稿值;
    - 项目材料本身不提供危害程度 (材料缺口);
    - **官方数据层可以算**: GBZ/T 230—2025 官方 PDF 已在本地
      (acquire/raw/nhc_gbz/pdfs/GBZ_T_230_2025.pdf), 附录 B 明确
      「我国已颁布的化学品分类与本标准表1的对应关系」→ 7 项分项指标
      全部可由 GHS 分类 (ghs_class 表, mem.gov.cn 危化品分类信息表, 2832 条) 映射。

设计纪律:
  1. **数据来源优先级**按标准 A.2.1: GHS 分类数据 → 国家技术标准 → 官方数据。
     本模块只用 ghs_class (GHS) + oel_limit/health_effect (国家标准) + 沸点等理化常数。
  2. **缺项双算法 + 透明标注** (2026-10-14 修正):
     - 标准「注6」: 缺乏蓄积性/致癌性/致敏性/生殖毒性数据的分项**暂按类别1 (4分) 赋分**;
     - 但 skill 已实测注6 兜底会让 THI **系统性虚高** (氨: GHS 按公式算 82→极度危害,
       真稿实为高度危害Ⅲ级) —— 因为多数物质 8 项里只有 1~2 项有数据。
     → 本模块**同时给出两个值**: `thi_note6` (按标准注6, 法定算法) 与
       `thi_zero` (缺项按0, 下界), 并输出 `missing` 清单与 `have` 计数。
       调用方**优先用 thi_note6** (标准算法), 但当 `have < 4` (数据严重不足) 时
       返回 None → 标待补充, 不输出可能失真的分级。
     → detail 里逐项标明「注6兜底」还是「实测」, 供人工复核。
  3. **CAS 精确匹配优先** (skill 教训: 「丙酮」曾被子串匹配到「丙酮氰醇」→ 剧毒误配)。
  4. 输出带完整 detail (每项 F/k/来源), 便于人工复核与溯源。

用法:
    from web.thi_rating import rate_substance
    r = rate_substance(conn, "丙酮", cas="67-64-1")
    if r: r["level"], r["thi"], r["detail"]
"""
from __future__ import annotations

# ─── 表1 分项指标权重 (GBZ/T 230—2025 表1, 逐项与标准原文核对) ───
WEIGHTS = {
    "acute_inhalation": 5,   # 急性吸入 (气体/蒸气/粉尘烟雾)
    "acute_dermal": 2,       # 急性经皮 (注1: 无经皮数据且不经皮吸收 → 类别5)
    "irritation": 3,         # 皮肤和眼刺激与腐蚀性
    "sensitization": 3,      # 致敏性
    "repro": 3,              # 生殖毒性
    "carcinogenicity": 5,    # 致癌性
    "diffusivity": 3,        # 扩散性
    "accumulation": 1,       # 蓄积性
}

# 分级界值 (标准 5.3)
GRADES = [
    (35, "轻度危害", 1),
    (50, "中度危害", 2),
    (65, "高度危害", 3),
    (float("inf"), "极度危害", 4),
]

# 实测项数下限: 8 项里至少 4 项有实测数据才输出分级 (低于此 → 数据严重不足)
# 2026-10-14 用户拍板「方案B」定 4:
#   实测两种算法的结果对比 (14 物质):
#     物质          实测项  注6兜底THI   下界THI   真稿(同公司另一产线,仅参照)
#     甲醇            3      71 极度      23 轻度    中度危害
#     甲苯            3      60 高度      24 轻度    中度危害
#     苯乙烯           4      50 高度      34 轻度    中度危害
#     苯醌            4      77 极度      29 轻度    —
#     邻苯二甲酸酐      3      66 极度      30 轻度    —
#     马来酸酐         3      66 极度      30 轻度    —
#     N,N-二甲基苯胺   3      68 极度      20 轻度    —
#   → 注6 兜底系统性虚高 (8项缺5项时, 5项×4分×权重=虚高30+), 与真稿普遍差 2 级;
#     下界算法系统性偏低 (缺项按0), 但方向诚实: 实测项少→值低→促人工复核。
#   用户选 B = 下界为主输出 + 注6 值入 basis 参照 + **实测<4 项一律不输出**。
#   后果: 14 个目标物质里仅 3 个 (苯乙烯/苯醌 + 3项实测的甲醇等被排除) 可写,
#         「丙酮/乙酸乙酯/二甲苯/二聚环戊二烯」因 GHS 缺急性毒性分类而实测仅2项
#         → 维持待补充 (官方数据层覆盖缺口, 非算法问题, 已如实上报)。
MIN_HAVE = 4


def grade_of(thi: float) -> tuple[str, int]:
    for bound, name, code in GRADES:
        if thi < bound:
            return name, code
    return "极度危害", 4


# ─── GHS 分类文本 → 表1 类别/分值 映射 (附录 B.1) ───
# 皮肤或眼刺激与腐蚀性: 皮肤刺激/腐蚀 1A~1C, 眼刺激腐蚀1 → 类别1 (4分)
#                       皮肤刺激/腐蚀 2, 眼刺激 2A → 类别2 (3分)
#                       皮肤刺激/腐蚀 3, 眼刺激 2B → 类别3 (2分)
#                       无 → 类别5 (0分)
IRRITATION_MAP = [
    (("皮肤腐蚀/刺激,类别1", "严重眼损伤/眼刺激,类别1"), 4),
    (("皮肤腐蚀/刺激,类别2", "严重眼损伤/眼刺激,类别2"), 3),
    (("皮肤腐蚀/刺激,类别3", "严重眼损伤/眼刺激,类别3"), 2),
]
# 呼吸或皮肤过敏物: 呼吸道致敏 1 → 类别1 (4分); 皮肤致敏 1 → 类别2 (3分)
SENS_MAP = [("呼吸道致敏物,类别1", 4), ("皮肤致敏物,类别1", 3)]
# 生殖毒性: 生殖毒性类别/生殖细胞致突变性 1A → 1 (4分); 1B → 2 (3分); 类别2 → 3 (2分)
REPRO_MAP = [
    ("生殖毒性,类别1a", 4), ("生殖细胞致突变性,类别1a", 4),
    ("生殖毒性,类别1b", 3), ("生殖细胞致突变性,类别1b", 3),
    ("生殖毒性,类别2", 2), ("生殖细胞致突变性,类别2", 2),
    ("生殖毒性,类别3", 1), ("生殖细胞致突变性,类别3", 1),
]
# 致癌性: 类别1A → 1 (4分); 1B → 2 (3分); 类别2 → 3 (2分)
CARC_MAP = [("致癌性,类别1a", 4), ("致癌性,类别1b", 3), ("致癌性,类别2", 2)]


def _norm_ghs(text: str) -> str:
    """GHS 分类文本规范化: 去空白/全角/小写化 (gzh 表里写法不统一)"""
    if not text:
        return ""
    t = str(text).replace(" ", "").replace("　", "").replace("\n", "")
    return t.lower()


def _score_from_ghs(hazard_raw: str, kind: str) -> tuple[int | None, str]:
    """按附录 B.1 从 GHS 分类文本取分项分值 F (无数据返回 None)"""
    t = _norm_ghs(hazard_raw)
    if not t:
        return None, "GHS无分类数据"
    if kind == "irritation":
        for keys, f in IRRITATION_MAP:
            for k in keys:
                if _norm_ghs(k) in t:
                    return f, f"GHS {k}"
        # 只标了眼/皮肤刺激但类别更低 → 类别4 (1分)
        if "皮肤腐蚀/刺激" in t or "严重眼损伤/眼刺激" in t:
            return 1, "GHS 刺激类别低(判类别4)"
        return None, "GHS无刺激/腐蚀分类"
    if kind == "sensitization":
        for k, f in SENS_MAP:
            if _norm_ghs(k) in t:
                return f, f"GHS {k}"
        return None, "GHS无致敏性分类"
    if kind == "repro":
        best_f: int | None = None
        best_why = "GHS无生殖毒性分类"
        for k, f in REPRO_MAP:
            if _norm_ghs(k) in t:
                if best_f is None or f > best_f:
                    best_f, best_why = f, f"GHS {k}"
        return best_f, best_why
    if kind == "carcinogenicity":
        for k, f in CARC_MAP:
            if _norm_ghs(k) in t:
                return f, f"GHS {k}"
        return None, "GHS无致癌性分类"
    if kind == "acute":
        # 急性毒性三类取最高分 (注1: 某一指标有多种取值时以最低取值分级 → 取最低类别=最高分)
        scores = []
        for k, f in (("急性毒性-经口,类别1", 4), ("急性毒性-经口,类别2", 3),
                     ("急性毒性-经口,类别3", 2), ("急性毒性-经口,类别4", 1),
                     ("急性毒性-吸入,类别1", 4), ("急性毒性-吸入,类别2", 3),
                     ("急性毒性-吸入,类别3", 2), ("急性毒性-吸入,类别4", 1),
                     ("急性毒性-经皮,类别1", 4), ("急性毒性-经皮,类别2", 3),
                     ("急性毒性-经皮,类别3", 2), ("急性毒性-经皮,类别4", 1)):
            if _norm_ghs(k) in t:
                scores.append((f, k))
        if not scores:
            return None, "GHS无急性毒性分类"
        scores.sort(reverse=True)
        return scores[0]
    return None, "未支持的分项"


# ─── 理化常数 (沸点/物态) — 用于扩散性判类 (表1 第7行) ───
# 只收本项目/常见物质, 每条给出来源类型; 查不到的返回 None (不猜)
BP_C = {
    "丙酮": 56.1, "甲醇": 64.7, "甲苯": 110.6, "苯": 80.1, "二甲苯": 138.4,
    "乙二醇": 197.3, "乙酸乙酯": 77.1, "2-丁酮": 79.6, "丁酮": 79.6,
    "二聚环戊二烯": 170.0, "马来酸酐": 200.0, "邻苯二甲酸酐": 284.0,
    "对苯二甲酸": 402.0, "苯乙烯": 145.2, "苯醌": 174.0, "双酚A": 220.0,
    "N,N-二甲基苯胺": 193.0, "乙苯": 136.2, "正己烷": 68.7, "环己烷": 80.7,
    "异丙醇": 82.3, "乙醇": 78.4, "三氯乙烯": 87.2, "二氯甲烷": 39.6,
    "四氯化碳": 76.8, "乙酸乙烯酯": 72.7, "环氧乙烷": 10.4, "甲醛": -19.0,
}
# 常温常压物态 (用于判 气态/液态/固态)
STATE = {
    "丙酮": "液态", "甲醇": "液态", "甲苯": "液态", "苯": "液态", "二甲苯": "液态",
    "乙二醇": "液态", "乙酸乙酯": "液态", "2-丁酮": "液态", "丁酮": "液态",
    "二聚环戊二烯": "液态", "马来酸酐": "固态", "邻苯二甲酸酐": "固态",
    "对苯二甲酸": "固态", "苯乙烯": "液态", "苯醌": "固态", "双酚A": "固态",
    "N,N-二甲基苯胺": "液态", "乙苯": "液态", "正己烷": "液态", "环己烷": "液态",
    "异丙醇": "液态", "乙醇": "液态", "三氯乙烯": "液态", "二氯甲烷": "液态",
    "四氯化碳": "液态", "乙酸乙烯酯": "液态", "环氧乙烷": "气态", "甲醛": "气态",
    "一氧化碳": "气态", "氨": "气态", "氯": "气态", "二氧化硫": "气态",
    "氮氧化物": "气态", "硫化氢": "气态", "苯胺": "液态",
}


def score_diffusivity(name: str) -> tuple[int | None, str]:
    """扩散性 (表1): 气态→4; 液态挥发性高(沸点<50)→4; 中(50~<150)→3; 低(≥150)→2;
       固态扩散性极低→1; 固体无数据→None (不猜)
    """
    st = STATE.get(name)
    bp = BP_C.get(name)
    if st == "气态":
        return 4, "常温常压气态(表1 类别1)"
    if st == "液态":
        if bp is None:
            return None, "液态但沸点未知"
        if bp < 50:
            return 4, f"液态沸点{bp}℃<50(类别1)"
        if bp < 150:
            return 3, f"液态沸点{bp}℃ 50~<150(类别2)"
        return 2, f"液态沸点{bp}℃≥150(类别3)"
    if st == "固态":
        # 酸酐/醌/对苯二甲酸: 使用时可见尘雾但很快落下 → 类别3 (2分)
        if name in ("马来酸酐", "邻苯二甲酸酐", "苯醌", "对苯二甲酸"):
            return 2, f"固态{name} 使用可见粉尘但很快落下(类别3)"
        if name == "双酚A":
            return 2, "固态双酚A 粉末状(类别3)"
        return None, f"固态{name} 扩散性数据不足"
    return None, "物态未知"


def score_accumulation(name: str) -> tuple[int | None, str]:
    """蓄积性 (表1, 权重1): 生物半减期/蓄积系数数据本项目不内置 →
       无数据返回 None (调用方按缺项处理, 不猜)"""
    return None, "蓄积性数据未内置(需文献/数据库)"


def _find_ghs(conn, name: str, cas: str | None) -> dict | None:
    """ghs_class 精确匹配: CAS 优先 (skill 教训: 禁止无约束子串)"""
    try:
        if cas:
            r = conn.execute("SELECT name, alias, cas, hazard_raw, is_highly_toxic, "
                             "is_carcinogen FROM ghs_class WHERE cas=? LIMIT 1",
                             (cas,)).fetchone()
            if r:
                return {"name": r[0], "alias": r[1], "cas": r[2], "hazard_raw": r[3],
                        "is_highly_toxic": r[4], "is_carcinogen": r[5], "match": "CAS精确"}
        # 名称等值 (禁止子串)
        for cand in [name] + [a.strip() for a in (name or "").split("/") if a.strip()]:
            r = conn.execute("SELECT name, alias, cas, hazard_raw, is_highly_toxic, "
                             "is_carcinogen FROM ghs_class WHERE name=? LIMIT 1",
                             (cand,)).fetchone()
            if r:
                return {"name": r[0], "alias": r[1], "cas": r[2], "hazard_raw": r[3],
                        "is_highly_toxic": r[4], "is_carcinogen": r[5], "match": "名称精确"}
        # 常见别名 (括号内容, 如「苯乙烯[稳定的]」)
        r = conn.execute("SELECT name, alias, cas, hazard_raw, is_highly_toxic, "
                         "is_carcinogen FROM ghs_class WHERE name LIKE ? LIMIT 1",
                         (name + "[%]",)).fetchone()
        if r:
            return {"name": r[0], "alias": r[1], "cas": r[2], "hazard_raw": r[3],
                    "is_highly_toxic": r[4], "is_carcinogen": r[5], "match": "名称+方括号"}
    except Exception:
        return None
    return None


def rate_substance(conn, name: str, cas: str | None = None) -> dict | None:
    """按 GBZ/T 230—2025 计算单一物质危害程度

    返回 None = 数据不足, 调用方应标「待补充」(宁缺勿错)
    返回 dict = {thi, level, grade, detail:[(指标, F, k, 依据)], basis, source, matched}
    """
    if not name:
        return None
    g = _find_ghs(conn, name, cas)
    if not g:
        return None

    detail: list[tuple[str, int | None, int, str]] = []
    thi_note6 = 0.0   # 标准注6 算法: 缺项按类别1 (4分) — 法定算法
    thi_zero = 0.0    # 缺项按0 — 下界
    have = 0
    missing: list[str] = []

    def _acc(label: str, f: int | None, k: int, why: str, note6_ok: bool = True):
        """登记一个分项: 实测值 → 两种算法都加; 缺项 → thi_note6 按注6 (4分, 若适用)
        / thi_zero 加0"""
        nonlocal thi_note6, thi_zero, have
        detail.append((label, f, k, why))
        if f is None:
            missing.append(label)
            if note6_ok:
                thi_note6 += 4 * k   # 注6: 暂按类别1 (4分)
            # thi_zero 不变
        else:
            thi_note6 += f * k
            thi_zero += f * k
            have += 1

    # 1) 急性吸入 (k=5) — 注1: 无吸入数据按经口分级, 权重同急性吸入
    t = _norm_ghs(g["hazard_raw"])
    f, why = _score_from_ghs(g["hazard_raw"], "acute")
    if f is None and "急性毒性-经口" in t:
        f, why = _score_from_ghs(g["hazard_raw"], "acute")
        why = why + " (注1: 无吸入数据按经口分级)"
    _acc("急性吸入", f, WEIGHTS["acute_inhalation"], why, note6_ok=False)

    # 2) 急性经皮 (k=2) — 注1: 无经皮数据且不经皮吸收 → 类别5 (0分)
    f2, why2 = _score_from_ghs(g["hazard_raw"], "acute")
    if f2 is None:
        why2 = "GHS无经皮急性毒性 (注1: 无数据且不经皮吸收按类别5=0分)"
    _acc("急性经皮", f2, WEIGHTS["acute_dermal"], why2, note6_ok=False)

    # 3) 皮肤和眼刺激与腐蚀性 (k=3)
    f3, why3 = _score_from_ghs(g["hazard_raw"], "irritation")
    _acc("皮肤和眼刺激与腐蚀性", f3, WEIGHTS["irritation"], why3, note6_ok=False)

    # 4) 致敏性 (k=3) — 注6 适用
    f4, why4 = _score_from_ghs(g["hazard_raw"], "sensitization")
    _acc("致敏性", f4, WEIGHTS["sensitization"], why4)

    # 5) 生殖毒性 (k=3) — 注6 适用
    f5, why5 = _score_from_ghs(g["hazard_raw"], "repro")
    _acc("生殖毒性", f5, WEIGHTS["repro"], why5)

    # 6) 致癌性 (k=5) — 注6 适用
    f6, why6 = _score_from_ghs(g["hazard_raw"], "carcinogenicity")
    _acc("致癌性", f6, WEIGHTS["carcinogenicity"], why6)

    # 7) 扩散性 (k=3) — 理化常数 (注6 不适用, 无数据则 thi_note6 也不加)
    f7, why7 = score_diffusivity(name)
    _acc("扩散性", f7, WEIGHTS["diffusivity"], why7, note6_ok=False)

    # 8) 蓄积性 (k=1) — 注6 适用
    f8, why8 = score_accumulation(name)
    _acc("蓄积性", f8, WEIGHTS["accumulation"], why8)

    total_items = len(WEIGHTS)
    if have < MIN_HAVE:
        return None  # 实测项不足 → 数据严重不足, 不输出分级

    level, grade = grade_of(thi_note6)
    level_z, grade_z = grade_of(thi_zero)
    # 主输出 = 下界 (诚实方向: 数据少→值低→促人工复核); 注6 值仅入 basis 供参照
    return {
        "thi": round(thi_zero, 1),
        "thi_note6": round(thi_note6, 1),
        "thi_zero": round(thi_zero, 1),
        "level": level_z,
        "grade": grade_z,
        "level_zero": level_z,
        "grade_zero": grade_z,
        "level_note6": level,
        "grade_note6": grade,
        "detail": detail,
        "have": have,
        "missing": missing,
        "matched": g["match"],
        "matched_name": g["name"],
        "matched_cas": g["cas"],
        "basis": (f"GBZ/T 230—2025 表1 THI计算; 分项指标由GHS分类(附录B.1)映射, "
                  f"扩散性用理化常数; 实测{have}/{total_items}项"
                  f"(缺{','.join(missing)}); "
                  f"缺项按0计THI={round(thi_zero,1)}→{level_z}; "
                  f"若按标准注6(缺项按类别1)THI={round(thi_note6,1)}→{level}"),
        "source": f"gbz_t_230_2025_thi + ghs_class({g['match']})",
    }


if __name__ == "__main__":
    import os
    import sqlite3
    import sys
    os.chdir('/Users/abaaba/Projects/ohs-report')
    sys.path.insert(0, '.')
    conn = sqlite3.connect('data/ohs.db')
    for nm, cs in [("丙酮", "67-64-1"), ("甲醇", "67-56-1"), ("甲苯", "108-88-3"),
                   ("乙酸乙酯", "141-78-6"), ("二聚环戊二烯", "77-73-6"),
                   ("苯醌", "106-51-4"), ("邻苯二甲酸酐", "85-44-9"),
                   ("马来酸酐", "108-31-6"), ("二甲苯", "1330-20-7"),
                   ("苯乙烯", "100-42-5"), ("乙二醇", "107-21-1"),
                   ("双酚A", "80-05-7"), ("对苯二甲酸", "100-21-0"),
                   ("N,N-二甲基苯胺", "121-69-7")]:
        r = rate_substance(conn, nm, cs)
        if not r:
            print(f"{nm:<14} → 数据不足 (None)")
            continue
        print(f"{nm:<14} THI={r['thi']:<6} {r['level']} (grade {r['grade']}) "
              f"have={r['have']} miss={r['missing']}")
        for k, f, w, why in r["detail"]:
            print(f"      {k:<12} F={str(f):<5} k={w} × {'' if f is None else f} = "
                  f"{'—' if f is None else f*w:<5} | {why}")
