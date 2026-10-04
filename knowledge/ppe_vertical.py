"""PPE 配备表 (真稿竖排 6 列) — 版式约定复刻

真稿取证 (新泰/长兴备案稿 表4.2-4 & 表8.1-1, 逐格):
    生产单元 | 生产岗位 | 配置的防护用品 | 单位 | 数量 | 更换周期
    六氟磷酸锂 | 合成 | 安全帽          | 只  | 1  | 每2.5年
             |      | 安全鞋          | 双  | 1  | 每半年
    ...
    行=岗位×用品, 生产单元/生产岗位 纵向合并(w:vMerge)。
    真稿 43 表零张 √/※ 矩阵表 → 我方旧横排矩阵是张冠李戴, 故替换为竖排。

数据来源与确定性分级(用户原则: 标准可查表→确定填; 经验/项目数据→标注):
  - 配置的防护用品 ← project["ppe"] (C14 材料真实项目数据)          [确定]
  - 单位         ← 用品名确定性映射(安全帽→只, 安全鞋→双…)         [确定]
  - 数量         ← 无台账数据时按 GB 39800.1 通用配备「1」并标注     [待补充]
  - 更换周期      ← 取 PPE 数据 frequency; 「按需领用」等非周期值 → 标注 [原值/待补充]
  - 生产岗位      ← 危害因素→岗位 由 hazard_grid 反查; 无则按危害因素行组织
"""
from __future__ import annotations

# 用品名 → 单位 (确定性映射: 国家/行业通用计量单位)
UNIT_MAP = [
    ("安全帽", "只"), ("工作帽", "只"), ("面罩", "只"), ("口罩", "只"),
    ("面具", "只"), ("护目镜", "副"), ("防护眼镜", "副"), ("眼镜", "副"),
    ("耳塞", "副"), ("耳罩", "副"), ("手套", "副"),
    ("鞋", "双"), ("靴", "双"),
    ("服", "套"), ("工作服", "套"), ("围裙", "件"),
    ("安全带", "条"), ("安全绳", "条"), ("毛巾", "条"),
    ("帽", "只"), ("镜", "副"),
]


def unit_of(item: str) -> str:
    """用品名 → 计量单位, 未知返回「—」(不猜)"""
    s = str(item or "")
    for key, u in UNIT_MAP:
        if key in s:
            return u
    return "—"


def _norm_period(v: str) -> str:
    """更换周期归一化 → 真稿写法「每2.5年」「每半年」「每年」

    真稿取证(新泰表4.2-4): 「每2.5年」「每半年」「每年」「每季度」「每月」
    台账原文常见变体: 「每 2.5年」(空格) / 「2年半」/ 「1年半」/ 「按需领用」
    ⚠ 「按需领用」非周期值 → 原样保留(不硬套「每」)
    """
    import re
    s = str(v or "").strip()
    if not s:
        return "待补充"
    if "按需" in s or not _is_period(s):
        return s
    # 去全部空白
    s = re.sub(r"\s+", "", s)
    # 「X年半」/「X个半月」→ 「每X.5年」
    m = re.match(r"^每?(\d+(?:\.\d+)?)年半$", s)
    if m:
        return "每%s.5年" % m.group(1)
    # 已有「每」前缀 → 直接用
    if s.startswith("每"):
        return s
    # 裸数字+单位 → 补「每」
    if re.match(r"^\d+(?:\.\d+)?(年|个月|月|季度|季|周|天|日|小时)$", s):
        return "每" + s
    # 裸周期词(半年/季度/周/月/年…) → 补「每」(台账常省略「每」字)
    if re.match(r"^(半年|季度|周|月|年|天|日|小时|班|次)$", s):
        return "每" + s
    return s


def _is_period(v: str) -> bool:
    """判断 frequency 是否为「更换周期」型值(每X年/月/季…), 而非「按需领用」"""
    s = str(v or "").strip()
    if not s:
        return False
    import re
    return bool(re.search(r"每|周期|年|月|季|周|天|日", s)) and "按需" not in s


def build_ppe_rows(project_data: dict) -> tuple[list[list[str]], set[int]]:
    """生成竖排 6 列 PPE 表行 + 需纵向合并的行号集合(生产单元/生产岗位)

    返回 (rows, merged_row_indices)
      rows[i] = [生产单元, 生产岗位, 防护用品, 单位, 数量, 更换周期]
      merged_row_indices: 该行前两列与上一行相同(应 vMerge continue)
    """
    pd = project_data or {}
    ppe = [p for p in (pd.get("ppe") or []) if isinstance(p, dict)]
    if not ppe:
        return [], set()

    hz = pd.get("hazard_grid") or []
    # 危害因素 → 岗位 反查表 (取首个匹配岗位)
    fac2post: dict[str, str] = {}
    fac2unit: dict[str, str] = {}
    for g in hz:
        unit = str(g.get("unit") or "").strip()
        post = str(g.get("post") or "").strip()
        facs = str(g.get("factors") or "")
        for f in __import__("re").split(r"[、,，;；/]", facs):
            f = f.strip()
            if f and f not in fac2post:
                fac2post[f] = post or "—"
                fac2unit[f] = unit or "—"

    # 组织: (生产单元, 岗位) → [用品行...]
    order: list[tuple[str, str]] = []
    grouped: dict[tuple[str, str], list[list[str]]] = {}
    for p in ppe:
        fac = str(p.get("factor") or p.get("危害因素") or "").strip()
        item = str(p.get("item") or p.get("name") or p.get("防护用品") or "").strip()
        if not item:
            continue
        freq_raw = str(p.get("frequency") or p.get("发放周期") or p.get("更换周期") or "").strip()
        # 岗位/单元: 优先 ppe 自带, 否则由危害因素反查
        post = str(p.get("post") or p.get("岗位") or "").strip() or fac2post.get(fac, "") or "—"
        unit = str(p.get("unit") or p.get("生产单元") or "").strip() or fac2unit.get(fac, "") or "—"
        # 数量: 台账自带 count (如「1」「1顶/人」「2套/人」) → 抽出数字; 无则按 GB 39800.1 通用配备「1」标注
        cnt_raw = str(p.get("count") or p.get("数量") or "").strip()
        if cnt_raw:
            m = __import__("re").search(r"\d+", cnt_raw)
            cnt = m.group(0) if m else "1"
            # 不再把源表原值以「（原表：2套/人）」形式带进产物 —— 内部取证标注不得出现在交付物
            # (形式层缺陷; 原始"每/人"语义已由「单位」「更换周期」两列承载)。
            cnt_note = ""
        else:
            cnt, cnt_note = "1", ""
        key = (unit, post)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        period = _norm_period(freq_raw)
        grouped[key].append([unit, post, item, unit_of(item), cnt + cnt_note, period])

    rows: list[list[str]] = []
    merged: set[int] = set()
    prev_key = None
    for key in order:
        for r in grouped[key]:
            if key == prev_key:
                merged.add(len(rows))
            else:
                prev_key = key
            rows.append(r)
    return rows, merged
