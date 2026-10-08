"""表骨架注册表 — 导入时把 36 张表骨架全部建出来 (含"待补充"占位)

设计 (用户定, 2026-09):
  表 = 报告的骨架, 很多章节都有表, 所以表非常重要。
  拿到材料时就该知道要建哪些表 → 像往表格里填空一样, 能填的填满, 缺的标"待补充"。

现状问题: build_all_tables 只产 8 张 (数据规则表+气象), 其余 28 张
  (检查表类/限值表类/类比类) 根本没在"填空"阶段建 —— 导出时才由
  section_filler 现算。骨架先行名不副实。

三类表 (判定复用 coverage.py 的 _DATA_DRIVEN, 单一权威源):
  data     数据驱动 — 材料给了才有行, 缺则 rows=[] 标待补充
  standard 标准库驱动 — 从 oel_limit/gbz1_rule/ppe_work_matrix 等直出 (可建)
  assess   评估链路驱动 — 需危害判定/评估结果 (导入时可能还没算 → 标待评估)

产出: build_skeletons(data, assess=None) -> {表名: {cols, rows, prov, status}}
  status: "filled" 有数据 | "await_data" 待补材料 | "await_assess" 待评估
"""
from __future__ import annotations

import re
import json

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 标准库驱动表 → 建表函数在此模块内实现 (从库直出)
# 评估链路驱动表 → 需 assess (危害判定/限值), 导入时未算则占位
_ASSESS_DRIVEN = {
    "危害因素识别表", "健康影响表", "物理因素健康影响表", "关键控制点表",
    "接触限值表", "室内空气质量标准表",
    "类比可比性表", "类比PPE配备表", "类比PPE有效性表", "类比工作日写实表",
    "劳动强度分级表", "卫生特征分级表", "周边环境表", "应急物资清单",
}
# 注1: 应急物资清单 在 2.1.1/7.1 两处出现; 数据来自 emergency_supplies 提取字段,
#      但 2.1.1 处常为现状应急物资 → 归评估/数据混合, 导入时能填则填
# 注2: 噪声/高温接触限值表 已移出本集 → 数据源是 oel_limit 标准库 (GBZ 2.2),
#      由 build_standard_tables 直接建 (真实报告里这张表列国标限值, 与项目检测无关)

# 检查表类: 数据源 = GBZ 1 标准库条款 (_CHECK_COLS 四列), 不依赖项目评估结果。
# 2026-09 修正: 原先归 await_assess → 面板显示"待评估填充", 但实测 18 张里
# 绝大多数的数据源是 gbz1_rule 标准库 (纯查表), 导入时即可建 → status='standard'.
# 2026-10 修正: 「检查结果/评价」两列原硬编码 "待确认"/"待评价" 并错称与真实报告一致;
#   真稿取证 (7 张表/59 行, 56 行有值: 符合46/合格9/基本符合1) 推翻该说法。
#   现按条款号套 knowledge.check_result_templates 定式 (零 LLM, 可追溯, 单一产地)。
_STANDARD_DRIVEN = {
    "选址检查表", "总体布局检查表", "建筑卫生学检查表", "辅助用室检查表",
    "辅助用室设置表", "管理制度检查表", "防护设施检查表",
    "应急救援检查表", "PPE拟配置检查表", "PPE配备表", "工艺检查表",
    "设备布局检查表",
}
# 注: 卫生特征分级表 同时是标准库(GBZ1分级)与评估驱动, 归 _ASSESS_DRIVEN 优先

# 检查表统一样板 (4 列; 结果/评价列留人工把关 — 与 fill_section 同构)
_CHECK_COLS = ["卫生要求", "检查依据", "检查结果", "评价"]

# 检查表 → gbz1_rule.theme (标准库查询键)
# 数据源单一: 标准库条款 (含 GBZ 1 条款号, 真实可追溯), 不硬编码要求文本
_CHECK_THEME = {
    "选址检查表": ["选址"],
    "总体布局检查表": ["总体布局"],
    "建筑卫生学检查表": ["建筑卫生学"],
    "辅助用室检查表": ["辅助用室"],
    "工艺检查表": ["防尘防毒", "防毒"],
    "设备布局检查表": ["总体布局", "防噪声振动"],
    "防护设施检查表": ["防尘防毒", "防噪声振动", "防暑防寒", "防腐", "通风"],
    "应急救援检查表": ["应急救援"],
    "管理制度检查表": [],          # 管理制度无 GBZ1 theme → 由项目 management 字段填
    "PPE拟配置检查表": [],
    "PPE配备表": [],
    "辅助用室设置表": ["辅助用室"],
}


def _build_check_table(name: str, conn) -> dict | None:
    """从 gbz1_rule 标准库建检查表 (结果/评价列留空 = 人工把关, 同真实报告)

    标准库是单一数据源: 条款换版只改库, 不改代码 (与 MUST_COVER 同原则)。
    """
    themes = _CHECK_THEME.get(name)
    if not themes:
        return None
    rows = []
    for th in themes:
        for r in conn.execute(
                "SELECT clause, rule FROM gbz1_rule WHERE theme=? ORDER BY clause", (th,)):
            clause, rule = str(r[0] or ""), str(r[1] or "")
            if not rule:
                continue
            # 检查结果/评价两列: 按条款号套定式模板 (单一产地 knowledge.check_result_templates,
            # 零 LLM, 可追溯)。真稿取证 46 行句式 → 未命中则诚实回退 "待补充"/"待评价"。
            # (2026-10 事故修正: 此前硬编码 "待确认"/"待评价", 注释错称与真实报告一致)
            from knowledge.check_result_templates import resolve as _cr
            _clause_key = str(clause).strip()
            _res, _vd = _cr(_clause_key)
            rows.append([rule, f"GBZ 1—2010 {clause}",
                         _res or "待补充", _vd or "待评价"])
    if not rows:
        return None
    from web.number_provenance import prov_std
    return {"cols": list(_CHECK_COLS), "rows": rows,
            "prov": prov_std(f"gbz1_rule:{'/'.join(themes)}")}


# 物理因素接触限值表 → oel_limit 库 (GBZ 2.2) 的因子名
# 2026-09: 数据源是**标准库**不是项目检测 — 真实报告里这张表列的是国标限值表,
# 与项目是否做过检测无关 (项目没测也要列限值供比对)。
_PHYS_LIMIT_FACTOR = {
    "噪声接触限值表": ["噪声", "脉冲噪声"],
    "高温接触限值表": ["高温"],
}


def _build_phys_limit_table(name: str, conn) -> dict | None:
    """从 oel_limit 建物理因素接触限值表 (GBZ 2.2 条款, 可追溯)"""
    factors = _PHYS_LIMIT_FACTOR.get(name)
    if not factors:
        return None
    rows = []
    for f in factors:
        for r in conn.execute(
                "SELECT factor_name, oel_type, value, unit, conditions, source_standard "
                "FROM oel_limit WHERE factor_name=? ORDER BY oel_type", (f,)):
            nm, otype, val, unit, cond, src = (str(x or "") for x in r)
            rows.append([nm, otype, f"{val} {unit}".strip(), cond, src or "GBZ 2.2"])
    if not rows:
        return None
    from web.number_provenance import prov_std
    return {"cols": ["危害因素", "接触限值类型", "限值", "适用条件", "依据标准"],
            "rows": rows, "prov": prov_std(f"oel_limit:{'/'.join(factors)}")}


def build_standard_tables(data: dict, conn=None,
                          section_of: dict | None = None) -> dict:
    """建"标准库驱动"的表 (检查表类 + 物理限值表) —— 导入时即可填

    用户定: 表=报告骨架, 拿到材料就该建好。这类表的数据源是标准库, 与项目无关,
    所以不该等到"评估完成"才出现。

    ⚠ 失败不静默: 单表建失败记入 _errors 并打印 (排查期), 不整体吞异常。
      (曾因 except 全吞 + SQL 列名写错(source vs source_standard), 表现为"表空")
    """
    out: dict = {}
    so = section_of or {}
    errs: list[str] = []
    try:
        if conn is None:
            from knowledge.oel import connect as _c
            conn = _c()
    except Exception as e:
        print(f"[table_skeleton] 标准库连接失败: {e}")
        return out
    jobs = ([(n, _build_check_table) for n in sorted(_STANDARD_DRIVEN)]
            + [(n, _build_phys_limit_table) for n in sorted(_PHYS_LIMIT_FACTOR)])
    for name, fn in jobs:
        try:
            t = fn(name, conn)
        except Exception as e:
            errs.append(f"{name}: {type(e).__name__}: {e}")
            continue
        if t:
            t["status"] = "filled"
            t["section"] = so.get(name, "")
            out[name] = t
    if errs:
        print(f"[table_skeleton] 标准库表建失败 {len(errs)} 张: " + "; ".join(errs[:5]))
    return out


def _skeleton_names() -> list[tuple[str, str]]:
    """骨架名单 (表名, 首个挂载小节) — 权威源 = word_export._SUB_TABLE_MAP"""
    from web.word_export import _SUB_TABLE_MAP
    seen: dict[str, str] = {}
    for sub, m in sorted(_SUB_TABLE_MAP.items()):
        if not m:
            continue
        for name in m[1]:
            seen.setdefault(name, sub)
    return list(seen.items())


def _data_driven() -> set[str]:
    from web.coverage import _DATA_DRIVEN
    return set(_DATA_DRIVEN)


def _prov(status: str, why: str) -> dict:
    from web.number_provenance import prov_rule
    return prov_rule({"file": "骨架先行(导入时建表)", "page": None, "text": why})


def _sanitize_untrusted(data: dict) -> dict:
    """清洗不可信字段 → **单一合并点**

    与 field_projection.UNTRUSTED_FIELDS 同源。表骨架是"数据落盘的唯一入口"
    (app.py:843 build_skeletons), 在此清一次, 下游 filler/导出/prompt 全部受益,
    不必每处各打一次补丁。

    当前唯一判据: 投资额无单位 (材料只给裸数字, 无"万/亿/元") → 口径不可确定。
    实测事故: 长兴 investment="5000" → 表「项目投资总额|万元|5000」+ 正文
    「投资总额5000万元」, 其中"万元"是系统/LLM 替材料编的口径。
    """
    out = dict(data)
    for _k in ("investment", "ohy_investment"):
        v = out.get(_k)
        s = str(v or "").strip()
        if s and not re.search(r"万|亿|元", s):
            out[_k] = ""            # 值置空 → 表填"待补充", prompt 也不投喂
            out.setdefault("_untrusted_fields", {})[_k] = (
                "材料中该数据未标注单位，无法确定口径")
    return out


def build_skeletons(data: dict, assess: dict | None = None) -> dict:
    """按骨架名单建全部表 (能填的填, 缺的占位)

    复用 build_data_tables 已建好的表 (不重复建), 只补缺失的骨架。
    外部取数表 (气象) 放"filling"占位, 由 external_tables 后台补齐 — 不阻塞导入。
    """
    from web.table_builder import build_data_tables
    from web.external_tables import EXTERNAL_TABLES, placeholder as _ext_ph

    data = _sanitize_untrusted(data)        # ← 不可信字段清洗 (单一合并点)
    built = dict(build_data_tables(data))
    # 标准库驱动表 (检查表类): 导入时即填 (GBZ1 条款查表, 与项目无关)
    names_map = dict(_skeleton_names())
    std_tables = build_standard_tables(data, section_of=names_map)
    built.update(std_tables)
    dd = _data_driven()
    out: dict[str, dict] = {}

    for name, section in _skeleton_names():
        if name in built:
            t = dict(built[name])
            t.setdefault("status", "filled" if t.get("rows") else "await_data")
            out[name] = t
            continue

        # 外部取数表 (需联网/LLM): 占位, 后台填 (用户设计: 异步联网 + 同时建表)
        # ⚠ 修复 (2026-10): 旧实现无条件重建占位 → **每次 build_skeletons 调用**
        #   (导入/评估回填/重建工具) 都把已填的 filled 表打回空占位 → 数据反复丢失,
        #   实测库中多个项目气象表永久卡 filling/rows=0。现在: 旧值已有行且地区未变
        #   → 保留旧值 (含其 prov 溯源); 无行/地区变了 → 重建占位, 交给缓存快补/后台线程。
        if name in EXTERNAL_TABLES:
            _old = (data.get("built_tables") or {}).get(name)
            if isinstance(_old, dict) and (_old.get("rows") or []):
                _cur_region = ""
                if name == "气象因素表":
                    try:
                        from web.external_tables import _region_of as _rg
                        _cur_region = _rg(data)
                    except Exception:
                        _cur_region = ""
                # 旧表带 region 且与新地区不一致 → 不保留 (地区变=数据过期, 重新取)
                _keep = (not _cur_region) or (not _old.get("region")) \
                    or (_old.get("region") == _cur_region)
                if _keep:
                    t = dict(_old)
                    t["section"] = t.get("section") or section
                    out[name] = t
                    continue
            # 缓存快补: 同步可得 (缓存命中/材料事实) → 直接填, 不进后台队列
            # (事故 2026-10: 卡 filling 的项目在评估回填/重建时也应能自愈)
            try:
                from web.external_tables import fill_from_cache as _ffc
                _t = _ffc(name, data)
                if _t and (_t.get("rows") or []):
                    _t["section"] = _t.get("section") or section
                    out[name] = _t
                    continue
            except Exception:
                pass
            ph = _ext_ph(name)
            ph["section"] = section
            out[name] = ph
            continue

        if name in dd:
            # 数据驱动但材料没给 → 空骨架, 诚实标待补充
            out[name] = {
                "cols": [], "rows": [], "section": section,
                "status": "await_data",
                "prov": _prov("await_data", "数据驱动表: 材料未提供对应数据, 待补充"),
            }
        elif name in _STANDARD_DRIVEN:
            out[name] = {
                "cols": list(_CHECK_COLS), "rows": [], "section": section,
                "status": "await_assess",
                "prov": _prov("await_assess", "标准库驱动: 导出时按标准库条款生成"),
            }
        else:
            out[name] = {
                "cols": [], "rows": [], "section": section,
                "status": "await_assess",
                "prov": _prov("await_assess", "评估链路驱动: 需危害判定/评估结果"),
            }

    # 评估链路驱动表: 传了 assess 就**当场填**, 别留空占位。
    # (2026-09: 此前 build_skeletons 收了 assess 参数却从不使用 → 这些表永远 0 行,
    #  导出只能靠 fill_section 现算; 且 assess 数据不落库, 面板显示"待评估"。
    #  实测: 38 条判定/17 条分级就绪时, 危害因素识别表/健康影响表/接触限值表/
    #  PPE配备表/管理制度检查表 等都能填, 却全是空壳。)
    if assess:
        _fill_assess_tables(out, assess)
    return out


# 评估驱动表 → fill_section 的 fill_ch (章号), 按章批量取一次再分发 (避免重复算)
_ASSESS_FILL_CH = {
    "5": ("危害因素识别表", "健康影响表", "物理因素健康影响表",
          "接触限值表", "噪声接触限值表", "高温接触限值表"),
    "8": ("PPE配备表", "PPE拟配置检查表", "类比PPE配备表", "类比PPE有效性表"),
    "9": ("管理制度检查表",),
    "10": ("关键控制点表",),
    "11": ("室内空气质量标准表",),
    "4": ("劳动能力分级表", "类比可比性表", "劳动强度分级表"),
}


def _fill_assess_tables(out: dict, assess: dict) -> None:
    """用 assess 结果填充"评估链路驱动"的骨架表 (就地更新 out)

    只填**当前为空**的表, 不覆盖已有数据 (骨架优先原则)。
    """
    try:
        from knowledge.oel import connect as oc
        from web.section_filler import fill_section
        conn = oc()
    except Exception:
        return
    for ch, names in _ASSESS_FILL_CH.items():
        try:
            tables = fill_section(conn, ch, assess)
        except Exception:
            continue
        _by = {t.get("name"): t for t in (tables or []) if isinstance(t, dict)}
        for nm in names:
            cur = out.get(nm)
            t = _by.get(nm)
            if not t or not (t.get("rows") or []):
                continue
            if cur and (cur.get("rows") or []):
                # ⚠ 已有数据的骨架**默认不覆盖**, 但有一类必须刷新: 引用的标准号已废止。
                #   事故 (2026-10): 噪声/高温接触限值表骨架建于 GBZ 2.2—2007 时代,
                #   标准升 2019 后骨架"永生" → 导出物里 21 处废止引用。
                #   判据: 骨架内容含**已废止标准号**而现算版本不含 → 用现算覆盖 (标准版本护栏)。
                _cs = json.dumps(cur, ensure_ascii=False)
                _ns = json.dumps(t, ensure_ascii=False)
                # ⚠ 前缀分支必须用 () 包住 —— 写成 "GB(?:/T|/Z)?|GBZ(?:/T)?\s*\d+..."
                #   会被 | 劈成两个完整模式, 左支无后续约束 → 只匹配到 "GB" (实测)。
                _RE_STD = (r"(?:GB(?:/T|/Z)?|GBZ(?:/T)?)\s*"
                           r"\d+(?:\.\d+)?\s*[—\-–−－]\s*\d{4}")
                _norm = lambda s: re.sub(r"[—\-–−－]", "-", s).replace(" ", "")
                _cur_std = {_norm(x) for x in re.findall(_RE_STD, _cs)}
                _new_std = {_norm(x) for x in re.findall(_RE_STD, _ns)}
                if _cur_std and _cur_std - _new_std:
                    # 骨架引用了现算没有的标准号 → 查库确认该号已废止才刷 (不误伤)
                    try:
                        import sqlite3 as _sq
                        from pathlib import Path as _P2
                        _c2 = _sq.connect(str(_P2(__file__).resolve().parent.parent / "data" / "ohs.db"))
                        _dead = False
                        for _c in _cur_std - _new_std:
                            _r = _c2.execute(
                                "SELECT state FROM standard_db WHERE REPLACE(REPLACE(code,' ',''),char(12288),'')=?",
                                (_c,)).fetchone()
                            if _r and (_r[0] or "") in ("废止", "已废止"):
                                _dead = True
                                break
                        _c2.close()
                    except Exception:
                        _dead = False
                    if _dead:
                        out[nm] = {
                            "cols": t.get("cols") or [],
                            "rows": t.get("rows") or [],
                            "section": (cur or {}).get("section", ""),
                            "status": "filled",
                            "prov": _prov("rule", f"评估链路产物 (章{ch}) · 标准版本护栏刷新"),
                        }
                        continue
                continue          # 已有数据且标准未废止, 不覆盖
            out[nm] = {
                "cols": t.get("cols") or [],
                "rows": t.get("rows") or [],
                "section": (cur or {}).get("section", ""),
                "status": "filled",
                "prov": _prov("rule", f"评估链路产物 (章{ch})"),
            }


def skeleton_summary(tables: dict) -> dict:
    """骨架统计 (供面板/日志)"""
    from collections import Counter
    c = Counter(t.get("status", "unknown") for t in tables.values())
    return {"total": len(tables), **dict(c)}


if __name__ == "__main__":
    import json
    import sqlite3

    c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT data FROM project WHERE id=?", ("c8c7ff0a4d",)).fetchone()
    d = json.loads(row[0])
    sk = build_skeletons(d)
    print("骨架总数:", len(sk))
    print("统计:", json.dumps(skeleton_summary(sk), ensure_ascii=False))
    print()
    for n, t in sk.items():
        st = t.get("status")
        mark = {"filled": "✓", "await_data": "○", "await_assess": "△"}.get(st, "?")
        print(f"  {mark} {n:20} rows={len(t.get('rows') or []):3} §{t.get('section','')} [{st}]")
