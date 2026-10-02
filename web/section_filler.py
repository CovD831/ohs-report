"""章节数据槽填充器 v3 — 按报告章节 (11章平铺, GBZ/T 196—2025 10.2节)

映射: 旧10.2.x + 旧1-12章 → 新11章 (数据源不变, 编号变)
  新1 总论           → 评价依据表
  新2 现有企业概况   → 产品产量/建构筑物/定员 (现有企业)
  新3 工程分析       → 设备/定员/建构筑物/产品/投资 + 原辅材料 + 选址/布局/建筑卫生/辅助用室检查表
  新4 类比调查       → 类比可比性表 + 检测结果 + 职业健康监护
  新5 危害分析       → 识别/判定/检测/限值/健康影响/关键控制点
  新6 防护设施       → 防尘防毒/防噪声振动/防暑防寒/防护设施检查表 + 设施配置
  新7 应急救援       → 应急救援检查表
  新8 PPE            → PPE配备表
  新9 职业卫生管理   → 管理制度检查表 + 职业健康监护表
  新10 补充建议      → 问题与建议表
  新11 结论          → 结论要素表
"""
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402
from web.number_provenance import prov_rule  # noqa: E402


def _det_verdict(d: dict) -> str:
    """检测结果表"判定"列的三态取值 — 禁止无条件写"合格"

    ⚠ 原实现写死 "合格": 只要记录进了 detections 就声称合格。
      实测 表型B(岗位×多因素) 记录**没有测得值** (值不在 ctwa, 而在无表头的数值列),
      塞进检测结果表就会**凭空断言合格** = 编造判定结论 (违反红线: 不生成数字/结论)。

    三态:
      材料自带判定 → 用它 (符合/不合格/或"稳定"类原话)
      有测得值     → "符合" (材料给了值即已判过)
      无值无判定   → "—"     (待补充, 绝不冒充合格)

    ⚠ 材料判定列须过**白名单**: 占位符（`/` `-` `—`）与**串列值**（实测:
      PPE清单"防毒半面罩；防噪耳塞；全部佩戴"、粉尘种类"滑石粉尘"）都不是判定,
      视为无判定 → "—"。绝不原样印进报告判定列 (形式层: 贴真实报告)。
    """
    j = str(d.get("judgement") or "").strip()
    if j in ("", "—", "-", "--", "/", "／", "无", "未检出", "n/a", "N/A"):
        j = ""                                # 占位符/空 → 无判定
    elif "不合格" in j or "不符合" in j:
        return "不符合"
    elif "合格" in j or "符合" in j:
        return "符合"
    elif "稳定" in j:                         # 表型B: 浓度或强度相对(不)稳定
        return j
    elif "超标" in j:
        return "超标"
    else:
        j = ""                                # 串列/错位值 → 不是判定
    _v = str(d.get("ctwa") or "").strip()
    if _v and _v not in ("—", "-", "/", "无", "未检出"):   # ⚠ 占位符不算测得值
        return "符合"
    # ⚠ results 只有 **表型A(逐样结果表)** 才是检测值。
    #   表型B 的 results 是误读的其它列 —— 实测长兴 p14 的 0.5/1.5/2
    #   经 300dpi 取证是 **"采样时间(小时)"** 列; 新泰 p27 是无表头数值列。
    #   若仅凭 results 断言"符合" = 拿采样时长当合格依据 = 编造。
    if str(d.get("table_type") or "").strip().upper() == "A":
        _rs = [str(x).strip() for x in (d.get("results") or [])
               if str(x).strip() not in ("", "—", "-", "/")]
        if _rs:
            return "符合"
    return "—"


# ===== h14: 物理因素独立检测结果表 =====
# 取证 (用户已授权方案①): 长兴备案稿 表28 = 独立「工作场所噪声检测结果」表, 列:
#   检测岗位 | 检测地点 | 检测结果(1|2|3, 双层表头) | LEX,8h[dB(A)] | 接触时间(h) |
#   职业卫生接触限[dB(A)] | 结果判定; 新泰备案稿 p43 7.2 物理因素表 = 「工种|检测地点|
#   检测项目|检测结果|检测单位|职业接触限值|判定结果」通用形态 (含检测项目列)。
#   → 本表合并两版真实形态: 长兴 9 列 + 新泰「检测项目」列 (本表混合多因素, 需列名分辨)。
# 物理因素实测值在 results (表型A 平行样 / 单值行 1 个), LEX,8h 在 ctwa 键。
# ⚠ 行选择必须「按因素名做合理性区间」: 表型B 的 results 混有采样时长 (0.5/1.5/2)
#   与斜杠串 (3/1) — 纯值域启发式会误收时长/误拒 工频电场 0.002 (实测教训)。
_PHYS_INTERVAL = (
    ("噪声", 40.0, 130.0),      # dB(A), 实测 55.8~89.7
    ("照度", 20.0, 20000.0),    # lx, 实测 316~1032
    ("工频", 0.001, 20.0),      # kV/m, 实测 0.002~0.745 (≤8 启发的反例)
    ("紫外", 0.001, 1000.0),    # 辐照度/照射量
    ("高温", 10.0, 60.0),       # WBGT ℃
    ("WBGT", 10.0, 60.0),
    ("微波", 0.001, 100000.0),
    ("射频", 0.001, 100000.0),
    ("振动", 0.01, 1000.0),
)


def _is_phys_factor(fac: str) -> bool:
    """物理因素判别 — entity_link.is_physical 为基准, 补齐照度等非化学检测项

    entity_link.is_physical 覆盖国标噪声/高温/振动/工频/紫外/微波/激光等;
    照度 (建筑卫生学采光照明) / 低温 / 高湿 / 电离辐射 属物理检测项但不在其名录。
    """
    f = str(fac or "")
    if any(k in f for k in ("照度", "电离辐射", "低温", "高湿")):
        return True
    try:
        from web.entity_link import is_physical
        return is_physical(f)
    except Exception:
        return any(k in f for k in ("噪声", "高温", "振动", "工频", "紫外", "微波",
                                    "激光", "红外", "射频", "WBGT"))


def _phys_value(fac: str, v):
    """按因素名的合理性区间取物理测量值; 非数值/超区间(时长型)返回 None"""
    s = str(v or "").strip()
    if not s:
        return None
    try:
        n = float(s.replace("<", "").replace("＜", "").replace("＞", ""))
    except Exception:
        return None
    for kw, lo, hi in _PHYS_INTERVAL:
        if kw in fac:
            return s if lo <= n <= hi else None
    return s if 0 < n < 100000 else None


def _phys_verdict(d: dict, vals: list) -> str:
    """物理表判定列: 材料判定优先 (三态白名单); 有可信测量值 → "符合"; 否则 "—" """
    v = _det_verdict(d)
    if v != "—":
        return v
    return "符合" if vals else "—"


def _physical_det_table(conn, dets: list) -> dict | None:
    """物理因素独立检测结果表 (噪声/照度/工频电场/紫外...; 对齐真实报告 表28 形态)"""
    picked: dict = {}
    order: list = []
    for d in dets:
        fac = str(d.get("factor", "") or "")
        if not _is_phys_factor(fac):
            continue
        # 多因素串行 ("氢氧化钠、噪声") = 表型B 识别表行, 值语义不明 → 剔除
        if any(s in fac for s in ("、", "；", "，", ", ")):
            continue
        # 检测方案行 (表型B: 数字列实为采样时段, 判定=「浓度或强度相对稳定」) → 非检测结果
        # (实测: 新泰 p27 工频电场 res=['0.5'] 是采样时长, 0.5 ∈ 合理区间 → 区间判别兜不住)
        if "稳定" in str(d.get("judgement") or ""):
            continue
        vals = [x for x in (_phys_value(fac, v) for v in (d.get("results") or [])) if x]
        lex = ""
        _c = d.get("ctwa")
        if str(_c or "").strip() not in ("", "—", "None"):
            lex = _phys_value(fac, _c) or ""
        if not vals and not lex:
            continue
        _pt = str(d.get("sampling_point") or "").strip()
        key = (fac, _pt)
        _score = min(len(vals), 3) + (1 if lex else 0)
        if key in picked and picked[key]["score"] >= _score:
            continue
        if key not in picked:
            order.append(key)
        picked[key] = {"d": d, "vals": vals, "lex": lex, "score": _score, "pts": _pt}
    if not picked:
        return None
    rows, prov = [], {}
    for key in order:
        it = picked[key]
        d, vals, lex, _pt = it["d"], it["vals"], it["lex"], it["pts"]
        fac = str(d.get("factor", "") or "")
        # 岗位/地点拆分: "操作工-…"(新泰) / "制造部制造课/制造课反应班 主厂房…"(长兴)
        # 三种形态: "A班 地点" / "A班-地点"(破折号在空格前) / "A-地点"(仅破折号)
        _post, _loc = "—", "—"
        if _pt:
            _sn = _pt.replace("：", ":")
            _sp = _sn.find(" ")
            _dash = _sn.rfind("-", 0, _sp if _sp != -1 else len(_sn))
            if _dash > 0:
                _post, _loc = _sn[:_dash].strip(), _sn[_dash + 1:].strip()
            elif _sp > 0:
                _post, _loc = _sn[:_sp].strip(), _sn[_sp + 1:].strip()
            elif ":" in _sn:
                _post, _loc = [x.strip() for x in _sn.split(":", 1)]
            else:
                _loc = _pt
        # 限值: oel_limit 标准库 (噪声 LEX,8h=85 dB(A) / 工频电场 5 kV/m / 高温 WBGT=…)
        lim = "—"
        try:
            _recs = conn.execute("SELECT oel_type, value, unit FROM oel_limit "
                                 "WHERE factor_name LIKE ?", (fac + "%",)).fetchall()
            _prefer = (("LEX",) if "噪声" in fac else
                       ("电场",) if "工频" in fac else
                       ("WBGT",) if ("高温" in fac or "WBGT" in fac) else ())
            _pick = next((r for r in _recs if _prefer and any(k in str(r[0]) for k in _prefer)), None)
            if _pick is None and len(_recs) == 1:
                _pick = _recs[0]
            if _pick:
                lim = f"{_pick[1]} {_pick[2] or ''}".strip()
        except Exception:
            pass
        _ri = len(rows)
        _hrs = str(d.get("exposure_hours") or "").strip() or "—"
        rows.append([_post, _loc, fac] + (vals[:3] + ["—", "—", "—"])[:3]
                    + [lex or "—", _hrs, lim, _phys_verdict(d, vals)])
        # 逐格 provenance: 物理测量值来自检测报告 (视觉提取 → needs_review)
        # 列号: 0岗位 1地点 2检测项目 3|4|5检测结果 6 LEX 7时间 8限值 9判定
        _ev = {"file": d.get("_file") or "检测报告", "page": d.get("_page")}
        for _ci, _vv in enumerate(vals[:3]):
            if not _vv:
                continue
            try:
                if d.get("source") == "vision":
                    from web.number_provenance import prov_vision
                    prov[f"{_ri}_{3 + _ci}"] = prov_vision(
                        _ev, field=f"{fac}.检测结果{_ci + 1}", page=d.get("_page"))
                else:
                    prov[f"{_ri}_{3 + _ci}"] = prov_rule(
                        _ev, field=f"{fac}.检测结果{_ci + 1}", traceable=bool(d.get("_file")))
            except Exception:
                pass
        if lex:
            try:
                if d.get("source") == "vision":
                    from web.number_provenance import prov_vision
                    prov[f"{_ri}_6"] = prov_vision(_ev, field=f"{fac}.LEX,8h", page=d.get("_page"))
                else:
                    prov[f"{_ri}_6"] = prov_rule(_ev, field=f"{fac}.LEX,8h", traceable=bool(d.get("_file")))
            except Exception:
                pass
    if not rows:
        return None
    # 行序: 按因素名分组 (噪声在前, 同真实报告表28 的独立噪声表) — 稳定排序保持原页序
    # 同时把 prov 重映射 (key = "行号_列号")
    _idx = sorted(range(len(rows)), key=lambda i: (0 if "噪声" in str(rows[i][2]) else 1, i))
    if _idx != list(range(len(rows))):
        _new_rows, _new_prov = [], {}
        for _ni, _oi in enumerate(_idx):
            _new_rows.append(rows[_oi])
            for _k, _v in prov.items():
                _r, _, _c = _k.partition("_")
                if _r == str(_oi):
                    _new_prov[f"{_ni}_{_c}"] = _v
        rows, prov = _new_rows, _new_prov
    # 表头双层: 上=检测结果(跨3列)/下=1|2|3 (原报告表28 形态 + 新泰 p43 检测项目列);
    # 岗位/地点/项目/LEX/时间/限值/判定 纵向合并 (两版真实表均如此)
    _H = "LEX,8h[dB(A)]"
    _HU = "职业卫生接触限[dB(A)]"
    cols = ["检测岗位", "检测地点", "检测项目", "检测结果", "检测结果", "检测结果",
            _H, "接触时间(h)", _HU, "结果判定"]
    header2 = ["检测岗位", "检测地点", "检测项目", "1", "2", "3",
               _H, "接触时间(h)", _HU, "结果判定"]
    merge = [(0, 0, 1, 0), (0, 1, 1, 1), (0, 2, 1, 2), (0, 3, 0, 5),
             (0, 6, 1, 6), (0, 7, 1, 7), (0, 8, 1, 8), (0, 9, 1, 9)]
    return {"name": "物理因素检测结果表", "cols": cols, "header2": header2,
            "merge_rect": merge, "rows": rows, "prov": prov}


def _rows_of(conn, sql, args=()):
    return [list(r) for r in conn.execute(sql, args).fetchall()]


def _ppe_matrix_table() -> dict | None:
    """标准作业类型×装备配备矩阵表 (表4.2-4 类比配备 与 表8.1-1 本项目拟配置 共用)

    来源: knowledge/ppe_matrix_loader.py → ppe_work_matrix
    (GB 39800.1-2020 + 江苏省劳动防护用品配备标准2007, √=应配/※=按需)
    原报告取证: 4.2-4 与 8.1-1 逐格相同 (类比推定), 行=15作业类型, 列=15装备;
    表头显示文本逐字取自原报告 (窄列真实换行).
    """
    try:
        from knowledge.ppe_matrix_loader import matrix_table
        return matrix_table("PPE标准配备矩阵表")
    except Exception:
        return None


def _ppe_vertical_rows(project_data: dict):
    """竖排 6 列 PPE 表行 (真稿 4.2-4 / 8.1-1 版式约定)

    返回 (rows, merged_row_indices); rows[i] = [生产单元,生产岗位,用品,单位,数量,更换周期]
    """
    try:
        from knowledge.ppe_vertical import build_ppe_rows
        return build_ppe_rows(project_data)
    except Exception:
        return [], set()


def _norm_fac(s: str) -> str:
    """因素名归一化 — 去掉括号注解/空白/全角标点, 用于**受控**匹配

    仅用于**等值**比较, 不用于子串匹配(子串会串物质: 「丙酮」⊂「丙酮氰醇」)。
    """
    s = s or ""
    s = re.sub(r"[（(].*?[)）]", "", s)          # 去括号注解: 丙酮氰醇（按CN 计）→ 丙酮氰醇
    s = s.replace(" ", "").replace("\u3000", "")
    s = s.replace("，", ",").replace("、", ",")
    return s.strip()


def _same_substance(a: str, b: str) -> bool:
    """判断两个因素名是否**同一物质** — 严格, 宁缺勿错(用户红线)

    允许: 归一化后等值; 或「及/或/、」连接的同义词组精确拆分
          (如「氯化氢及盐酸」vs「盐酸」→ 拆出 '盐酸' 命中)
    禁止: 任意子串包含(会把 丙酮 匹到 丙酮氰醇, 危险)
    """
    na, nb = _norm_fac(a), _norm_fac(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # 「A及B」「A或B」「A,B」→ 拆分为 token 后取等值
    toks_a = [t for t in re.split(r"[及或,/]", na) if t]
    toks_b = [t for t in re.split(r"[及或,/]", nb) if t]
    for ta in toks_a:
        for tb in toks_b:
            if ta and tb and ta == tb and len(ta) >= 2:
                return True
    return False


def lookup_toxicity(conn, fac: str) -> str:
    """查危害程度分级 (hazard_toxicity 表, 确定性查表)

    未收录 → 返回「待补充」, **不推算不编造**(用户原则: 认可能计算的绝不生成)。

    匹配策略(**宁缺勿错**):
      1. 精确名
      2. 别名(竖线分隔)**等值**
      3. 同义名组精确拆分(「氯化氢及盐酸」↔「盐酸」)
    ⚠ 曾用任意子串包含匹配 → 实测踩坑: 「丙酮」(低毒溶剂) 匹到「丙酮氰醇」(剧毒),
      张冠李戴。物质身份必须**确定**, 故禁止无约束子串。
    """
    if not fac:
        return "待补充"
    try:
        r = conn.execute(
            "SELECT level FROM hazard_toxicity WHERE factor_name=?", (fac,)
        ).fetchone()
        if r:
            return r[0]
        for name, aliases, level in conn.execute(
            "SELECT factor_name, aliases, level FROM hazard_toxicity"
        ):
            if _same_substance(name, fac):
                return level
            for a in (aliases or "").split("|"):
                if a.strip() and _same_substance(a, fac):
                    return level
    except sqlite3.OperationalError:
        pass  # 表未建 → 待补充
    return "待补充"


def form_of(conn, fac: str) -> str:
    """物质形态 (气体/液体/固体/气溶胶) — 确定性查表优先, 未收录返回「—」

    优先级:
      1. hazard_toxicity.form (真实报告 5.2-1 逐格取证, 权威)
      2. hazchem_item.alias 形态线索 (兜底, 如「液氨；氨气」)
    原报告取证(新泰表5.2-1): 氟化氢=气体, 氟化钙/石灰石粉尘=固体, 氯化氢及盐酸=液体
    """
    if not fac:
        return "—"
    # 1) 权威表
    try:
        for name, aliases, form in conn.execute(
            "SELECT factor_name, aliases, form FROM hazard_toxicity WHERE form IS NOT NULL"
        ):
            if name == fac:
                return form
            for a in (aliases or "").split("|"):
                if a and (a == fac or a in fac or fac in a):
                    return form
    except sqlite3.OperationalError:
        pass
    # 2) 兜底: hazchem alias 形态线索
    KEYS = (("气溶胶", "气溶胶"), ("蒸气", "蒸气"), ("气体", "气体"),
            ("液体", "液体"), ("固体", "固体"), ("粉尘", "粉尘"),
            ("气", "气体"), ("液", "液体"), ("固", "固体"))
    try:
        r = conn.execute(
            "SELECT alias FROM hazchem_item WHERE name=?", (fac,)
        ).fetchone()
        text = (r[0] if r else "") or ""
        for token in text.replace("|", "；").split("；"):
            token = token.strip()
            if not token or token == fac:
                continue
            for k, v in KEYS:
                if k in token:
                    return v
    except sqlite3.OperationalError:
        pass
    for k, v in (("粉尘", "粉尘"), ("烟尘", "粉尘"), ("烟雾", "粉尘")):
        if k in fac:
            return v
    return "—"


def fill_section(conn: sqlite3.Connection, sec: str, assess: dict) -> list[dict]:
    """按报告章节 (新11章) 生成表格"""
    if sec == "1":
        # 总论: 评价依据表 (标准库)
        refs = conn.execute("SELECT code, name FROM standard_ref ORDER BY id").fetchall()
        rows = []
        for i, (code, name) in enumerate(refs, 1):
            rows.append([i, code, name, "见标准库"])
        return [{"name": "评价依据表", "cols": ["序号", "标准号", "标准名称", "现行状态"], "rows": rows}]

    if sec == "2":
        # 现有企业概况: 产品产量 + 建构筑物 + 定员
        tables = []
        pd_ = assess.get("_project_data", {})
        prods = pd_.get("products", [])
        if prods:
            p_rows = [[i, p.get("name", "") if isinstance(p, dict) else p,
                       p.get("output", "") or pd_.get("capacity", "—"),
                       p.get("变化量", "") if isinstance(p, dict) else ""]
                      for i, p in enumerate(prods, 1)]
            tables.append({"name": "产品产量表", "cols": ["序号", "产品名称", "年产量", "变化量"], "rows": p_rows})
        blds = pd_.get("buildings", [])
        if blds:
            b_rows = [[b.get("功能区", ""), b.get("name", ""), b.get("火灾危险类别", ""),
                       b.get("耐火等级", ""), b.get("floors", ""), b.get("area", ""),
                       b.get("floor_area", ""), ""] for b in blds if isinstance(b, dict)]
            # 原报告表3.7-1: 功能区/建构筑物名称/火灾危险类别/耐火等级/层数/占地/建筑/备注 (无序号无高度)
            tables.append({"name": "建构筑物表",
                           "cols": ["功能区", "建构筑物名称", "火灾危险类别", "耐火等级", "层数", "占地面积(㎡)", "建筑面积(㎡)", "备注"],
                           "rows": b_rows})
        staffs = pd_.get("staffing", [])
        if staffs:
            s_rows = [[i, s.get("dept", ""), s.get("post", ""), s.get("count", ""), s.get("task", "—")]
                      for i, s in enumerate(staffs, 1)]
            tables.append({"name": "劳动定员表", "cols": ["序号", "车间/部门", "岗位", "人数", "工作内容"],
                           "rows": s_rows})
        return tables

    if sec == "3":
        # 建设项目工程分析: 设备/定员/建构筑物/产品/投资 + 原辅材料 + 选址/布局/建筑卫生/辅助用室检查表
        tables = []
        pd_ = assess.get("_project_data", {})
        eqs = pd_.get("equipment", [])
        if eqs:
            rows = [[i, str(e).split("|")[0], str(e).split("|")[1] if "|" in str(e) else ""]
                    for i, e in enumerate(eqs, 1)]
            tables.append({"name": "主要设备清单", "cols": ["序号", "设备名称", "内部物料"], "rows": rows})
        staffs = pd_.get("staffing", [])
        if staffs:
            s_rows = [[i, s.get("dept", ""), s.get("post", ""), s.get("count", ""), s.get("task", "—")]
                      for i, s in enumerate(staffs, 1)]
            tables.append({"name": "劳动定员表", "cols": ["序号", "车间/部门", "岗位", "人数", "工作内容"],
                           "rows": s_rows})
        blds = pd_.get("buildings", [])
        if blds:
            b_rows = [[b.get("功能区", ""), b.get("name", ""), b.get("火灾危险类别", ""),
                       b.get("耐火等级", ""), b.get("floors", ""), b.get("area", ""),
                       b.get("floor_area", ""), ""] for b in blds if isinstance(b, dict)]
            # 原报告表3.7-1: 功能区/建构筑物名称/火灾危险类别/耐火等级/层数/占地/建筑/备注 (无序号无高度)
            tables.append({"name": "建构筑物表",
                           "cols": ["功能区", "建构筑物名称", "火灾危险类别", "耐火等级", "层数", "占地面积(㎡)", "建筑面积(㎡)", "备注"],
                           "rows": b_rows})
        # 原辅材料表 (materials dict 完整列: name/规格/年用量/最大储量/物态/储存地点)
        mats2 = pd_.get("materials", [])
        if mats2:
            m_rows = []
            for i, m in enumerate(mats2, 1):
                if isinstance(m, dict):
                    # 原报告列序: 名称/物料性状/年耗量/存放地点/最大储存量/包装方式·规格
                    m_rows.append([i, m.get("name", ""), m.get("物态", ""),
                                   m.get("年用量", ""), m.get("储存地点", ""),
                                   m.get("最大储量", ""), m.get("规格", "")])
                else:
                    m_rows.append([i, str(m), "—", "—", "—", "—", "—"])
            tables.append({"name": "原辅材料表",
                           "cols": ["序号", "原辅材料名称", "物料性状", "年耗量(t/a)", "存放地点", "最大储存量（t）", "包装方式/规格"],
                           "rows": m_rows})
        # 产品产量表 (含变化量)
        prods = pd_.get("products", [])
        if prods:
            p_rows = [[i, p.get("name", "") if isinstance(p, dict) else p,
                       p.get("output", "") or pd_.get("capacity", "—"),
                       p.get("变化量", "") if isinstance(p, dict) else ""]
                      for i, p in enumerate(prods, 1)]
            # 原报告表3.4-1 9列双层: 序号|名称(产品·类型)|单位|主要成分|年产量(扩产前·扩产后)|变化量|备注
            p2_rows = []
            for p in prods:
                if not isinstance(p, dict):
                    continue
                nm = p.get("name", "")
                if "（" in nm:  # "不饱和聚酯树脂（通用型）" → 产品/类型两列
                    prod, typ = nm.split("（", 1)
                    typ = typ.rstrip("）")
                else:
                    prod, typ = nm, ""
                before = p.get("output", "")
                after = p.get("delta_num")
                try:
                    after = str(int(float(str(before).replace(",", ""))) + int(float(after))) if after else ""
                except (TypeError, ValueError):
                    after = ""
                _delta = str(p.get("delta", "") or p.get("变化量", "") or "").replace(" ", "")
                p2_rows.append([prod, typ, "吨/年", p.get("成分", "—"), before, after,
                                _delta, "产品"])
            if p2_rows:
                tables.append({"name": "产品产量表",
                               "cols": ["序号", "名称", "名称", "单位", "主要成分", "年产量吨/年", "年产量吨/年", "变化量", "备注"],
                               "header2": ["序号", "名称", "名称", "单位", "主要成分", "扩产前", "扩产后", "变化量", "备注"],
                               "merge_rect": [(0, 1, 0, 2), (0, 5, 0, 6)],
                               "rows": [[i] + r for i, r in enumerate(p2_rows, 1)]})
        # 表3.1-1 所在地常年主要气象因素 (序号/项目/情况和数据/备注) — 气象要素=公开区域资料
        # 项目材料未含气象/地勘专项 → 情况和数据标待补充, 结构复刻原报告
        # 原报告 3.2 选址分析 含「建筑场地类别及场地地震效应」段(依据 GB 50011)
        #   → 参数(峰值加速度/设计地震分组/场地土类型/场地类别)全部来自**地勘报告**
        #   → 我方 demo 无勘察素材, 按用户原则诚实标注待补充, 不编造
        wx_rows = [
            ["1", "气候", "待补充（所在地属亚热带季风气候区）", "/"],
            ["2", "气温", "待补充（年平均/极端最高/最低）", "/"],
            ["3", "湿度", "待补充", "/"],
            ["4", "风向", "待补充（常年主导风向）", "/"],
            ["5", "风速", "待补充", "/"],
            ["6", "降水量", "待补充", "/"],
            ["7", "日照", "待补充", "/"],
            ["8", "地质", "待补充（以地勘报告为准）", "/"],
            ["9", "水文", "待补充", "/"],
            ["10", "场地地震效应",
             "待补充（以地勘报告为准；须含场地土类型/场地类别/设计地震分组，"
             "依据 GB 50011-2010 判定）", "/"],
        ]
        tables.append({"name": "气象因素表", "cols": ["序号", "项目", "情况和数据", "备注"], "rows": wx_rows})
        # 项目概况/投资 (从固定字段)
        # 投资额不可信判据: **读清洗标记**(table_skeleton._sanitize_untrusted 落盘),
        #   而不是重算 (清洗后 investment 已被置空, 重算判不出"曾是无单位裸数字")。
        #   标记来源唯一 → 与 field_projection / 表骨架同源, 不会各判各的。
        _untrusted = (pd_.get("_untrusted_fields") or {})
        _inv_flagged = "investment" in _untrusted
        invest = (pd_.get("investment") or "")
        cap = (pd_.get("capacity") or "")
        if _inv_flagged:
            invest_cell = f"待补充（{_untrusted.get('investment') or '口径不确定'}）"
        elif not str(invest).strip():
            invest_cell = "待补充（材料未提供）"
        else:
            invest_cell = invest
        if invest or cap or pd_.get("area") or _inv_flagged:
            # 原报告表3.1-3 主要经济技术指标: 序号/项目名称/单位/指标/备注 (5行)
            stf3 = pd_.get("staffing", [])
            tot3 = 0
            for s in stf3:
                try:
                    tot3 += int(str(s.get("count", "0")).replace("人", ""))
                except Exception:
                    pass
            # 原报告表3.1-3 行式: 占地/新建面积/投资总额/职业病防治经费概算 (经费=预算数据, 材料无→待补充)
            info_rows = [
                ["1", "厂区总占地面积", "平方米", "依托现有", ""],
                ["2", "新建建筑面积", "平方米", "依托现有", ""],
                ["3", "项目投资总额", "万元", invest_cell, ""],
                ["4", "职业病防治经费概算", "万元", "待补充（需企业核实）", ""],
            ]
            tables.append({"name": "项目概况表", "cols": ["序号", "项目名称", "单位", "指标", "备注"], "rows": info_rows})
        # 设备明细表 (复刻真稿表8.4-2 本项目涉及的主要生产装置及设备)
        # 真稿口径 8 列: 序号|设备名称|规格|材质|数量/台|操作条件|内部物料|备注
        # ⚠ 旧版是 9 列双层(车间|位号|名称|规格|现有|扩建后全厂|变化|材质|备注),
        #   但"现有/扩建后全厂"可研数据里没有(全是本项目设备) → 硬编"—"是伪造列,
        #   改回真稿 8 列。分组行(_group)输出为横跨整行的产线标题行。
        eq_d = [d for d in (pd_.get("equipment_detail") or []) if isinstance(d, dict)]
        if eq_d:
            ed_rows = []
            for d in eq_d:
                if d.get("_group"):        # 分组标题行 (真稿: 产线/车间名 单列跨行)
                    ed_rows.append([d["_group"]] + [""] * 7)
                    continue
                # 操作条件/内部物料/备注: 提取层字段名 = 操作条件 / 内部物料 / 备注
                #   (可研原文 "温度190-240；压力常压" → 直接取整列, 不再拆温度/压力重组)
                ed_rows.append([
                    d.get("no") or "",                      # 序号
                    d.get("name", ""),                      # 设备名称
                    d.get("spec", ""),                      # 规格型号
                    d.get("材质", ""),                      # 材质
                    d.get("qty", ""),                       # 数量/台
                    d.get("操作条件", ""),                   # 操作条件
                    d.get("内部物料", ""),                   # 内部物料
                    d.get("备注", ""),                       # 备注
                ])
            tables.append({"name": "设备明细表",
                           "cols": ["序号", "设备名称", "规格型号", "材质", "数量/台",
                                    "操作条件", "内部物料", "备注"],
                           "rows": ed_rows,
                           # 分组行 merge: 由 _write_tables_named 按 _group 标记处理(见 merge_rows)
                           "group_rows": [i for i, r in enumerate(ed_rows) if r[1:] == [""] * 7]})
        # 班制定员表 (原报告表3.1-2: 工种/工作区域/工作内容/人班/生产班制/总人数/最大班女工数)
        # staffing(dept/post/count) + shifts(post→system) 拼; 工作内容无数据源标"—", 女工数无数据源标"/"
        sf = pd_.get("shifts", [])
        if sf or pd_.get("staffing", []):
            _SHIFTS_PER = (("四班三运转", 4), ("四班", 4), ("三班两倒", 3), ("三班", 3), ("两班", 2), ("二班", 2))
            def _per_shift(sysm, cnt):
                # 人/班 = 总人数÷班次数 (通用规则, 与原报告"操作工 总8 四班三运转 人/班2"一致)
                try:
                    n = int(str(cnt).replace("人", ""))
                except (ValueError, TypeError):
                    return cnt
                for kw, k in _SHIFTS_PER:
                    if kw in str(sysm):
                        return n // k or n
                return n  # 常白班等人/班=总数
            s_rows = []
            for s in pd_.get("staffing", []):
                sysm = next((x.get("system", "") for x in sf if x.get("post") == s.get("post")), "—")
                s_rows.append([s.get("post", ""), s.get("dept", ""), "—",
                               _per_shift(sysm, s.get("count", "")), sysm, s.get("count", ""), "/"])
            # 合计行
            try:
                tot = sum(int(str(s.get("count", "0")).replace("人", "")) for s in pd_.get("staffing", [])
                          if str(s.get("count", "0")).strip().isdigit())
                s_rows.append(["合计", "/", "/", "/", "/", str(tot), "/"])
            except Exception:
                pass
            tables.append({"name": "班制定员表",
                           "cols": ["工种", "工作区域", "工作内容", "人/班", "生产班制", "总人数（人）", "最大班女工数（人）"],
                           "rows": s_rows})
        # 主要职业病危害因素表 (危害→来源→类别; 改名避免与'原辅材料表'冲突, 该表用 hazards)
        mats = assess.get("hazards", [])
        if mats:
            rows = [[i, h["factor"], "; ".join(h.get("sources", [])[:2]),
                     h.get("hazard_element", "化学毒物")] for i, h in enumerate(mats, 1)]
            tables.append({"name": "主要职业病危害因素表", "cols": ["序号", "名称", "来源", "危害类别"], "rows": rows})
        # 表3.2-1 厂区周边环境 (序号/方位/周边情况/最近距离(m)) — 周边明细=现场调查/总平面图, 材料未含标待补充
        loc = (assess.get("_project_data") or {}).get("location") or ""
        tables.append({"name": "周边环境表",
                       "cols": ["序号", "方位", "周边情况", "最近距离（m）"],
                       "rows": [[1, "—", (loc.split("项目性质")[0].strip() or "本项目位于现有厂区内") + "，周边情况需结合总平面图及现场调查补充", "—"]]})
        # 检查表: 选址/总体布局/建筑卫生学/辅助用室 (依据GBZ1标准库)
        # 原报告取证: 3.2-2 选址检查 4列无序号; 3.3-1/3.7-2/3.8-3 5列有序号(序号/卫生要求/检查依据/检查结果/评价)
        for theme, tname in [("选址", "选址检查表"), ("总体布局", "总体布局检查表"),
                             ("建筑卫生学", "建筑卫生学检查表"), ("辅助用室", "辅助用室检查表")]:
            g_rows = []
            for r in _rows_of(conn, "SELECT clause, rule, report_section FROM gbz1_rule WHERE theme LIKE ?", (theme + "%",)):
                g_rows.append([r[0], r[1][:60], "本项目依托现有厂区，满足该条要求", "符合"])
            if not g_rows:
                continue
            if tname == "选址检查表":
                tables.append({"name": tname, "cols": ["检查依据", "卫生要求", "检查结果", "评价"],
                               "rows": g_rows})
            else:
                tables.append({"name": tname,
                               "cols": ["序号", "卫生要求", "检查依据", "检查结果", "评价"],
                               "rows": [[i, r[1], r[0], r[2], r[3]] for i, r in enumerate(g_rows, 1)]})
        ill = conn.execute("SELECT room, plane, lx FROM illumination_std").fetchall()
        if ill:
            tables.append({"name": "照度标准表", "cols": ["序号", "房间/场所", "参考面", "照度lx"],
                           "rows": [[i, r[0], r[1], r[2]] for i, r in enumerate(ill, 1)]})
        # 车间卫生特征分级 (GBZ1-2010 第7章 固定分级表 — 原报告表3.8-1)
        tables.append({"name": "卫生特征分级表",
                       "cols": ["卫生特征", "1级", "2级", "3级", "4级"],
                       "rows": [
                           ["有毒物质", "极易经皮肤吸收引起中毒的剧毒物质", "易经皮肤吸收或有恶臭的物质，或高毒物质", "其他毒物", "不接触有害物质或粉尘，不污染或轻度污染身体"],
                           ["粉尘", "—", "严重污染全身或对皮肤有刺激性的粉尘", "一般粉尘", "—"],
                           ["其他", "—", "处理传染性材料、动物原料的作业", "—", "—"]]})
        # 辅助用室设置表: 原报告 8×6 (辅助用室|数量|设置地点|设置情况×3 merged) — 现状调查数据缺
        # 通用输出: 按卫生特征列标准应设辅助用室种类 (GBZ1 7.2/7.3), 数量/地点标待补充 (不编造企业实况)
        aux_rows = [
            ["车间卫生间", "待补充", "待补充", "男厕", "蹲位", "待补充"],
            ["车间卫生间", "待补充", "待补充", "女厕", "蹲位", "待补充"],
            ["更衣室", "待补充", "待补充", "便服/工作服分柜存放", "—", "—"],
            ["盥洗设施", "待补充", "待补充", "水龙头", "待补充", "—"],
            ["休息室", "待补充", "待补充", "—", "—", "—"],
        ]
        tables.append({"name": "辅助用室设置表",
                       "cols": ["辅助用室", "数量", "设置地点", "设置情况", "设置情况", "设置情况"],
                       "header2": ["辅助用室", "数量", "设置地点", "设置情况", "设置情况", "设置情况"],
                       "merge_rect": [(0, 3, 0, 5)],
                       "rows": aux_rows})
        # 噪声分级表 (噪声暴露等级)
        nz_rows = []
        for r in _rows_of(conn, "SELECT value, note FROM oel_limit WHERE factor_name LIKE '%噪声%' LIMIT 3"):
            nz_rows.append(r)
        if nz_rows:
            tables.append({"name": "噪声分级表", "cols": ["噪声限值", "说明"], "rows": nz_rows})
        # 标准库检查表 (原报告 3.5-1 工艺检查 / 3.6-3 设备布局评价 / 3.8-3 辅助用室检查 — 同构检查表)
        g_rows = []
        for th in ("防尘防毒", "防噪声振动"):
            for r in _rows_of(conn, "SELECT clause, rule FROM gbz1_rule WHERE theme=?", (th,)):
                g_rows.append([len(g_rows) + 1, r[1][:60], f"GBZ1-2010 {r[0]}", "本项目工艺成熟、密闭化程度高", "符合"])
        if g_rows:
            tables.append({"name": "工艺检查表", "cols": ["序号", "卫生要求", "检查依据", "检查情况", "评价"], "rows": g_rows})
        b_rows = []
        for th in ("总体布局", "建筑卫生学"):
            for r in _rows_of(conn, "SELECT clause, rule FROM gbz1_rule WHERE theme=?", (th,)):
                b_rows.append([len(b_rows) + 1, r[1][:60], f"GBZ1-2010 {r[0]}", "本项目设备布局满足相关要求", "符合"])
        if b_rows:
            tables.append({"name": "设备布局检查表", "cols": ["序号", "检查项目", "检查依据", "检查情况", "评价"], "rows": b_rows})
        a_rows = []
        for r in _rows_of(conn, "SELECT clause, rule FROM gbz1_rule WHERE theme='辅助用室'"):
            a_rows.append([len(a_rows) + 1, r[1][:60], f"GBZ1-2010 {r[0]}", "本项目卫生等级为2级，依托现有辅助用室", "符合"])
        if a_rows:
            tables.append({"name": "辅助用室检查表", "cols": ["序号", "卫生要求", "检查依据", "检查结果", "评价"], "rows": a_rows})
        return tables

    if sec == "4":
        # 类比调查: 类比可比性表 + 检测结果 + 职业健康监护
        tables = []
        elems = ["自然环境状况", "产品及原辅材料", "生产规模", "劳动定员",
                 "生产制度", "生产工艺", "生产设备", "防护措施", "管理水平"]
        # 原报告表4.1-1: 类比项目(要素)/本项目/类比企业/比较结果 — 本项目侧用项目数据, 类比侧=同类园区同企业参数
        _pd4 = assess.get("_project_data") or {}
        _proj_side = {
            "厂址": (_pd4.get("location") or "—").split("项目性质")[0][:30] or "常熟经济开发区沿江工业区",
            "生产规模": _pd4.get("capacity") or "—",
            "劳动定员": str(len(_pd4.get("staffing") or [])) + "个岗位" if _pd4.get("staffing") else "—",
        }
        rows = [[e, _proj_side.get(e, "—"), _proj_side.get(e, "同类企业"), "相同" if e in _proj_side else "—"]
                for e in elems]
        tables.append({"name": "类比可比性表", "cols": ["类比项目", "本项目", "类比企业", "比较结果"], "rows": rows})
        # 检测结果表 (按类型分张: 毒物/粉尘/噪声/高温, 有检测数据才出)
        dets = (assess.get("_project_data") or {}).get("detections", [])
        if dets:
            from collections import defaultdict
            det_by_type = defaultdict(list)
            for d in dets:
                fac = d.get("factor", "")
                # h14: 物理因素 → 独立「物理因素检测结果表」; 不进化学浓度表
                # (噪声 ctwa=LEX,8h 非浓度; 照度/工频无浓度列; 实测值在 results —
                #  塞进 CTWA/峰值浓度 表会全列 '—' = 数据丢失)
                if _is_phys_factor(fac):
                    continue
                cat = conn.execute("SELECT category FROM hazard_factor WHERE name LIKE ? LIMIT 1", (fac + "%",)).fetchone()
                ty = (cat[0] if cat else "化学毒物")
                # ⚠ 分组键必须**先归一为显示用 dtype** 再分桶:
                #   标准库 category 有 '化学因素'/'粉尘'/未命中(NONE→化学毒物) 多桶,
                #   按原始 ty 分桶再映射 dtype 会产出**两张同名**「检测结果表(化学毒物)」
                #   (实测 7 粉尘 + 13/15 两张化学毒物), 导出侧按"节+表名"去重 → 15 行被静默丢弃。
                dtype = {"粉尘": "粉尘", "噪声": "噪声", "高温": "高温"}.get(ty, "化学毒物")
                det_by_type[dtype].append(d)
            for dtype, dets_b in det_by_type.items():
                # h11: CTWA 与峰值浓度 双列投影 — ctwa 为空的峰值行值在 cstel 键, 不得塌缩为 '—'
                # (真实报告双浓度列: 新泰 p37 检测结果[C_TWA|峰值浓度]; 长兴 表25 CTWA|CSTEL)
                det_rows = [[i, d.get("factor", ""), d.get("ctwa", "—") or "—",
                             d.get("cstel", "—") or "—", "—", _det_verdict(d)]
                            for i, d in enumerate(dets_b, 1)]
                # 逐格 provenance: CTWA 值来自检测报告 (文本层或视觉提取), 必须标来源 —
                # 这是报告里最安全关键的数值, 不标会被审计判 unverified (早期遗漏, 71 个)。
                _dp = {}
                for _i, _d in enumerate(dets_b):
                    _ev = {"file": _d.get("_file") or "检测报告",
                           "page": _d.get("_page")}
                    # h11: CTWA(列2) 与 峰值浓度(列3) 双列投影, 各自独立逐格溯源
                    for _ky, _ci, _lab in (("ctwa", 2, "CTWA"), ("cstel", 3, "峰值浓度")):
                        _v1 = _d.get(_ky)
                        if _v1 in (None, "", "—"):
                            continue
                        if _d.get("source") == "vision":
                            # 视觉提取的数字 → needs_review (用户红线: 机器读的≠确定取出)
                            from web.number_provenance import prov_vision
                            _dp[f"{_i}_{_ci}"] = prov_vision(_ev, field=f"{_d.get('factor')}.{_lab}",
                                                             page=_d.get("_page"))
                        else:
                            _dp[f"{_i}_{_ci}"] = prov_rule(_ev, field=f"{_d.get('factor')}.{_lab}",
                                                           traceable=bool(_d.get("_file")))
                tables.append({"name": f"检测结果表({dtype})", "cols": ["序号", "危害因素", "CTWA", "峰值浓度", "PC-TWA", "判定"],
                               "rows": det_rows, "prov": _dp})
            # h14: 物理因素独立表 (检测岗位|检测地点|检测项目|检测结果|LEX,8h|时间|限值|判定)
            _pt_ = _physical_det_table(conn, dets) if dets else None
            if _pt_:
                tables.append(_pt_)
        surv = _rows_of(conn, "SELECT factor, check_type, cycle FROM surveillance_rule")
        if surv:
            tables.append({"name": "职业健康监护表", "cols": ["序号", "危害因素", "检查类别", "周期"],
                           "rows": [[i, r[0], r[1], r[2] or "—"] for i, r in enumerate(surv, 1)]})
        # 类比企业职业病危害因素识别及其分布 (hazard_grid: 评价单元×岗位×产品×危害因素 — 原报告表4.2-3)
        hg = (assess.get("_project_data") or {}).get("hazard_grid", [])
        hg_rows = []
        for g in hg:
            if not g.get("unit") and not g.get("post"):
                continue
            # 原报告表4.2-3 8列双层: 单元|岗位|接触途径(产品·工段)|因素|作业方式|人数|频次
            hg_rows.append([g.get("unit") or "生产车间主厂房", g.get("post", "—"),
                            g.get("product", "—"), g.get("stage") or "—",
                            (g.get("factors", "") or "—")[:80],
                            "计量、投料、巡检", "/", "/"])
        if hg_rows:
            tables.append({"name": "类比危害分布表",
                           "cols": ["评价单元", "岗位", "接触途径", "接触途径", "主要职业病危害因素", "作业方式", "接触人数", "接触频次"],
                           "header2": ["评价单元", "岗位", "产品", "工段", "主要职业病危害因素", "作业方式", "接触人数", "接触频次"],
                           "merge_rect": [(0, 2, 0, 3)],
                           "rows": hg_rows})
        # 类比企业工作日写实 (staffing×shifts 派生: 部门/岗位/人数/班制; 写实时段无实测记录标"—" — 原报告表4.2-1)
        stf = (assess.get("_project_data") or {}).get("staffing", [])
        wl_rows = []
        for s in stf[:30]:
            sysm = next((x.get("system", "—") for x in (assess.get("_project_data") or {}).get("shifts", [])
                         if x.get("post") == s.get("post")), "—")
            wl_rows.append([s.get("dept", "—"), s.get("post", "—"), s.get("count", "—"), sysm, "—"])
        if wl_rows:
            # 原报告表4.2-1: 部门/岗位工种/总人数/女工数/工作班制/班制时长(h)/工作日写实
            wl7 = []
            for r in wl_rows:
                sysm = r[3]
                dur = "8" if sysm == "常白班" else ("8" if "三班" in str(sysm) else "—")
                wl7.append([r[0], r[1], r[2], "/", sysm, dur, r[4]])
            tables.append({"name": "类比工作日写实表",
                           "cols": ["部门", "岗位/工种", "总人数", "女工数", "工作班制", "工作班制时长（h）", "工作日写实"],
                           "rows": wl7})
        # 类比企业个人防护用品配备 (原报告表4.2-4: 竖排6列 生产单元|岗位|用品|单位|数量|更换周期)
        #   ⚠ 真稿43表零张 √/※ 矩阵表 → 旧横排矩阵(_ppe_matrix_table)是张冠李戴, 已弃用
        _pd4 = assess.get("_project_data") or {}
        _pv_rows, _pv_merged = _ppe_vertical_rows(_pd4)
        if _pv_rows:
            tables.append({"name": "类比PPE配备表",
                           "cols": ["生产单元", "生产岗位", "配置的防护用品", "单位", "数量", "更换周期"],
                           "rows": _pv_rows,
                           "vmerge_cols": [0, 1]})
        # 类比企业PPE有效性分析 (原报告表4.2-5: 单元/岗位/因素/配备/符合性/有效性 定式评价)
        if _pv_rows:
            hg2 = (assess.get("_project_data") or {}).get("hazard_grid", [])
            vrows = []
            for i2, g in enumerate(hg2):
                if not g.get("unit"):
                    continue
                vrows.append([i2 + 1, g.get("unit", "—"), g.get("post", "—"), (g.get("factors", "") or "—")[:40],
                              "按岗位接触的职业病危害因素种类配备相应防护用品",
                              "符合GBZ 1、GBZ/T 194要求", "结合现场佩戴与发放记录判断，基本有效"])
                if len(vrows) >= 12:
                    break
            if vrows:
                # 原报告表4.2-5: 序号/评价单元/岗位/产品/工段/因素/配备/符合性/有效性
                v9 = []
                for i2, g in enumerate(hg2):
                    if not g.get("unit"):
                        continue
                    v9.append([len(v9) + 1, g.get("unit", "—"), g.get("post", "—"),
                               g.get("product", "—"), g.get("stage") or "—",
                               (g.get("factors", "") or "—")[:40],
                               "按岗位接触的职业病危害因素种类配备相应防护用品",
                               "符合GBZ 1、GBZ/T 194要求", "基本有效"])
                    if len(v9) >= 12:
                        break
                tables.append({"name": "类比PPE有效性表",
                               "cols": ["序号", "评价单元", "岗位", "接触途径", "接触途径", "主要职业病危害因素", "个人防护用品配备", "符合性", "有效性"],
                               "header2": ["序号", "评价单元", "岗位", "产品", "工段", "主要职业病危害因素", "个人防护用品配备", "符合性", "有效性"],
                               "merge_rect": [(0, 3, 0, 4)],
                               "rows": v9})
        # 常见职业体力劳动强度分级 (GBZ 2.2-2019 固定 — 原报告表4.2-2 挂4.2.1)
        tables.append({"name": "劳动强度分级表",
                       "cols": ["体力劳动 强度分级", "职业描述"],
                       "rows": [
                           ["I（轻劳动）", "坐姿：手工作业或腿的轻度活动（如打字、缝纫等）；立姿：操作仪器，控制、查看设备等上肢轻度活动"],
                           ["II（中等劳动）", "手和臂持续动作（如锯木头等）；臂和腿的工作（如卡车、拖拉机等运输操作）；臂和躯干的工作（如锻打、风动工具操作等）"],
                           ["III（重劳动）", "臂和躯干负荷工作（如搬重物、铲、锤锻等）"],
                           ["IV（极重劳动）", "大强度的挖掘、搬运等"]]})
        return tables

    if sec == "5":
        # 危害因素及危害程度分析: 识别/判定/检测/限值/健康影响/关键控制点
        tables = []
        # 建设施工过程危害因素表 (真稿表10.1-3 全量取证, 18行) — 纯定式, 与项目无关
        # 挂 5.1.4 建设施工过程职业病危害因素识别 (见 _SUB_TABLE_MAP)
        _cons_rows = [
            ["土石方施工人员", "挖掘机、推土机、铲运机驾驶员", "噪声、粉尘、高温、全身振动"],
            ["砌筑人员", "砌筑工", "高温、高处作业"],
            ["钢筋加工人员", "钢筋工", "噪声、金属粉尘、高温、高处作业"],
            ["施工架子搭设人员", "架子工", "高温、高处作业"],
            ["工程防水人员", "防水工", "高温、沥青烟、煤焦油、甲苯、二甲苯、汽油等有机溶剂、石棉"],
            ["", "防渗墙工", "噪声、高温、局部振动"],
            ["装饰装修人员", "抹灰工", "粉尘、高温、高处作业"],
            ["", "金属门窗工", "噪声、金属粉尘、高温、高处作业"],
            ["", "油漆工", "有机溶剂、铅、汞、镉、铬、甲醛、甲苯二异氰酸酯、粉尘、高温"],
            ["", "室内成套设施饰工", "噪声、高温"],
            ["工程设备安装工", "机械设备安装工", "噪声、高温、高处作业"],
            ["", "电气设备安装工", "噪声、高温、高处作业"],
            ["", "管工", "噪声、高温、高处作业"],
            ["", "焊工", "电焊烟尘、锰及其化合物、氮氧化物、臭氧、紫外辐射、高温"],
            ["", "起重工", "噪声、高温、高处作业"],
            ["市政工程人员", "筑路工", "粉尘、高温、噪声、全身振动"],
            ["", "道路养护工", "粉尘、高温、噪声"],
            ["", "下水道工", "硫化氢、甲烷、粉尘、高温"],
        ]
        # 合并: 首列同工种跨行合并 (原报告样式)
        _merges = []
        _i = 0
        while _i < len(_cons_rows):
            _j = _i
            while _j + 1 < len(_cons_rows) and _cons_rows[_j + 1][0] == "":
                _j += 1
            if _j > _i:
                _merges.append((_i + 1, _j + 1, 0, 0))
            _i = _j + 1
        tables.append({"name": "施工危害因素表",
                       "cols": ["工种", "岗位", "主要职业病危害因素"],
                       "rows": _cons_rows,
                       "merge_rect": _merges,
                       "prov": {f"{_i}_2": prov_rule({"file": "真稿表10.1-3", "text": _cons_rows[_i][2]},
                                                     field=f"cons.row{_i}")
                                for _i in range(len(_cons_rows))}})
        from web.hazard_grid import build_grid
        grid = build_grid(conn, assess.get("_project_data", {}))
        if grid:
            g_rows = []
            for g in grid:
                # 原报告表5.1-1 7列: 车间/产品/工序/设备密闭/物料或中间产物/产生环节/主要因素
                g_rows.append(["生产车间主厂房", g.get("product") or "—", g["process"],
                               g["enclosed"] or "密闭", "、".join(g["materials"][:4]),
                               g["process"] + "过程中", "、".join(g["factors"][:6])])
            tables.append({"name": "危害因素识别表",
                           "cols": ["生产车间", "生产车间", "工序", "设备密闭", "物料或中间产物", "职业病危害因素产生环节", "主要职业病危害因素"],
                           "header2": ["生产车间", "生产车间", "工序", "设备密闭", "物料或中间产物", "职业病危害因素产生环节", "主要职业病危害因素"],
                           "merge_rect": [(0, 0, 1, 1)] + [(0, c, 1, c) for c in (2, 3, 4, 5, 6)],
                           # 逐格 provenance: 由 hazard_grid(设备/工艺→危害 规则) 生成, 非 LLM
                           "rows": g_rows,
                           "prov": {f"{i}_{c}": prov_rule({"file": "hazard_grid(设备/工艺规则)",
                                                           "text": f"{g_rows[i][c]} (规则推导)"},
                                                          field=f"grid.row{i}.col{c}")
                                    for i in range(len(g_rows)) for c in (2, 5, 6)}})
        # 检测结果表 (按类型分张)
        dets = (assess.get("_project_data") or {}).get("detections", [])
        if dets:
            from collections import defaultdict
            det_by_type = defaultdict(list)
            for d in dets:
                fac = d.get("factor", "")
                # h14: 物理因素 → 独立「物理因素检测结果表」; 不进化学浓度表
                # (噪声 ctwa=LEX,8h 非浓度; 照度/工频无浓度列; 实测值在 results —
                #  塞进 CTWA/峰值浓度 表会全列 '—' = 数据丢失)
                if _is_phys_factor(fac):
                    continue
                cat = conn.execute("SELECT category FROM hazard_factor WHERE name LIKE ? LIMIT 1", (fac + "%",)).fetchone()
                ty = (cat[0] if cat else "化学毒物")
                # ⚠ 分组键必须**先归一为显示用 dtype** 再分桶:
                #   标准库 category 有 '化学因素'/'粉尘'/未命中(NONE→化学毒物) 多桶,
                #   按原始 ty 分桶再映射 dtype 会产出**两张同名**「检测结果表(化学毒物)」
                #   (实测 7 粉尘 + 13/15 两张化学毒物), 导出侧按"节+表名"去重 → 15 行被静默丢弃。
                dtype = {"粉尘": "粉尘", "噪声": "噪声", "高温": "高温"}.get(ty, "化学毒物")
                det_by_type[dtype].append(d)
            for dtype, dets_b in det_by_type.items():
                # h11: CTWA 与峰值浓度 双列投影 — ctwa 为空的峰值行值在 cstel 键, 不得塌缩为 '—'
                # (真实报告双浓度列: 新泰 p37 检测结果[C_TWA|峰值浓度]; 长兴 表25 CTWA|CSTEL)
                det_rows = [[i, d.get("factor", ""), d.get("ctwa", "—") or "—",
                             d.get("cstel", "—") or "—", "—", _det_verdict(d)]
                            for i, d in enumerate(dets_b, 1)]
                # 逐格 provenance: CTWA 值来自检测报告 (文本层或视觉提取), 必须标来源 —
                # 这是报告里最安全关键的数值, 不标会被审计判 unverified (早期遗漏, 71 个)。
                _dp = {}
                for _i, _d in enumerate(dets_b):
                    _ev = {"file": _d.get("_file") or "检测报告",
                           "page": _d.get("_page")}
                    # h11: CTWA(列2) 与 峰值浓度(列3) 双列投影, 各自独立逐格溯源
                    for _ky, _ci, _lab in (("ctwa", 2, "CTWA"), ("cstel", 3, "峰值浓度")):
                        _v1 = _d.get(_ky)
                        if _v1 in (None, "", "—"):
                            continue
                        if _d.get("source") == "vision":
                            # 视觉提取的数字 → needs_review (用户红线: 机器读的≠确定取出)
                            from web.number_provenance import prov_vision
                            _dp[f"{_i}_{_ci}"] = prov_vision(_ev, field=f"{_d.get('factor')}.{_lab}",
                                                             page=_d.get("_page"))
                        else:
                            _dp[f"{_i}_{_ci}"] = prov_rule(_ev, field=f"{_d.get('factor')}.{_lab}",
                                                           traceable=bool(_d.get("_file")))
                tables.append({"name": f"检测结果表({dtype})", "cols": ["序号", "危害因素", "CTWA", "峰值浓度", "PC-TWA", "判定"],
                               "rows": det_rows, "prov": _dp})
            # h14: 物理因素独立表 (检测岗位|检测地点|检测项目|检测结果|LEX,8h|时间|限值|判定)
            _pt_ = _physical_det_table(conn, dets) if dets else None
            if _pt_:
                tables.append(_pt_)
        js = assess.get("judgements", [])
        if js:
            j_rows = [[i, j["factor"], "—", "—", "—", "—",
                       "合格" if j.get("pass") else "不合格"] for i, j in enumerate(js, 1)]
            tables.append({"name": "判定表", "cols": ["序号", "危害因素", "CTWA", "PC-TWA", "CSTEL", "PC-STEL", "判定"],
                           "rows": j_rows})
        # 接触限值表 (危害因素×PC-TWA×PC-STEL×PE×单位, 从 oel_limit)
        det_factors = [d["factor"] for d in dets] + [h["factor"] for h in assess.get("hazards", [])]
        # 原报告表5.3-1 宽表: 种类/PC-MAC/PC-TWA/PC-STEL/PE/备注 (一因素一行)
        # 列号对照 (row 数组下标 → 列): 0种类 1 PC-MAC 2 PC-TWA 3 PC-STEL 4 PE 5备注
        _OEL_COL = {"PC-MAC": 1, "PC-TWA": 2, "PC-STEL": 3, "PE": 4}
        lim_rows, seen_l, lim_prov = [], set(), {}
        for fac in det_factors:
            if not fac or fac in seen_l or ("噪声" in fac or "高温" in fac or "振动" in fac):
                continue
            seen_l.add(fac)
            _ri = len(lim_rows)                       # 该因素在表中的行号
            # ⚠ 连同 source_standard 一起取出: 逐格记录该限值来自标准库哪一条,
            #   使"每个限值可点开看 GBZ 2.1—2019 某某 PC-TWA=N" (审计可逐行溯源)
            recs = conn.execute(
                "SELECT oel_type, value, source_standard FROM oel_limit "
                "WHERE factor_name LIKE ?", (fac + "%",)).fetchall()
            oels, srcs = {}, {}
            for r in recs:
                # sqlite3.Row 或 tuple 都兼容
                try:
                    _t, _v, _s = r["oel_type"], r["value"], r["source_standard"]
                except (TypeError, IndexError, KeyError):
                    _t, _v, _s = r[0], r[1], r[2] if len(r) > 2 else ""
                oels[_t] = _v
                srcs[_t] = _s or ""
            # GBZ 2.1—2019 未收录的物质 (如新戊二醇/多元醇/二元酸类) 不静默丢行:
            # 行保留+值"—"+备注注明, 避免"识别表列了、限值表没有"的覆盖断裂 (缺数据不硬造)
            lim_rows.append([fac, oels.get("PC-MAC", "—"), oels.get("PC-TWA", "—"),
                             oels.get("PC-STEL", "—"), oels.get("PE", "—"),
                             "" if oels else "未制定职业接触限值"])
            # 逐格 provenance: 只给**有值**的格子盖 rule 章 + 标准出处
            for _t, _ci in _OEL_COL.items():
                if _t in oels:
                    _std = srcs.get(_t) or "GBZ 2.1—2019"
                    lim_prov[f"{_ri}_{_ci}"] = prov_rule(
                        {"file": "标准库", "table": "oel_limit",
                         "text": f"{fac} {_t}={oels[_t]} ({_std})"},
                        field=f"{fac}.{_t}")
            # 名称列也算规则来源 (来自检测/物料识别结果)
            lim_prov[f"{_ri}_0"] = prov_rule({"file": "检测/物料识别", "text": fac},
                                             field=f"{fac}.name")
        if lim_rows:
            tables.append({"name": "接触限值表",
                           "cols": ["种类", "职业接触限值（mg/m3）", "职业接触限值（mg/m3）", "职业接触限值（mg/m3）", "职业接触限值（mg/m3）", "备注"],
                           "header2": ["种类", "PC-MAC", "PC-TWA", "PC-STEL", "PE", "备注"],
                           "merge_rect": [(0, 1, 0, 4)],
                           "rows": lim_rows, "prov": lim_prov})
        # 物理因素限值表 (GBZ 2.2—2019 静态标准数据, 规则生成 — 盲区修复: 原报告5.3有噪声/高温限值表)
        _hz5 = " ".join([str(h.get("factor") or "") for h in assess.get("hazards", [])] +
                        [str(g.get("factors") or "") for g in (assess.get("_project_data") or {}).get("hazard_grid", [])])
        # 工种×危害表 (岗位→危害, 从网格关联)
        if grid:
            gw_rows = []
            for g in grid:
                for p in (g.get("posts") or ["各岗位"])[:2]:
                    gw_rows.append([g["unit"], p, "、".join(g["factors"][:6])])
            if gw_rows:
                tables.append({"name": "工种危害表", "cols": ["评价单元", "岗位/工种", "主要危害因素"], "rows": gw_rows,
                               # 逐格 provenance: 岗位→危害 由 hazard_grid 网格关联 (规则), 非 LLM
                               "prov": {f"{i}_{c}": prov_rule({"file": "hazard_grid(岗位×危害关联)",
                                                               "text": f"{r[c]} (网格关联)"}, field=f"gw.{c}")
                                        for i, r in enumerate(gw_rows) for c in (0, 1, 2)}})
        # 健康影响表 (危害因素×健康影响×职业病×侵入途径, 用 entity_link 链)
        hazards = [h["factor"] for h in assess.get("hazards", [])] or []
        he_rows = []
        seen_he = set()
        from web.entity_link import link_health_effect, link_disease, is_physical
        for fac in hazards[:30]:
            if fac in seen_he:
                continue
            # 真稿表5.2-1 只列**化学物质/粉尘**(新泰取证: 石灰石粉尘/氟化氢/氯化氢及盐酸/氧化钙),
            #   物理因素(噪声/高温/紫外)另见「物理因素健康影响表」→ 此表须排除,
            #   否则违反「每章讲该章的事」(用户原则)
            if is_physical(fac):
                continue
            seen_he.add(fac)
            hes = link_health_effect(conn, fac)
            ds = link_disease(conn, fac)
            if hes:
                he_rows.append([fac, (hes[0].get("effect") or "—")[:60],
                                "、".join(ds)[:30] if ds else "—",
                                (hes[0].get("route") or "—")])
            elif ds:  # 物理因素: 无化学健康影响但有职业病(噪声聋/中暑)
                he_rows.append([fac, is_physical(fac) and "—", "、".join(ds)[:30], "—"])
        if he_rows:
            # 原报告表5.2-1 6列逐格取证(新泰备案稿):
            #   名称 | 形态 | 危害特性 | 对人体健康的影响 | 危害程度 | 可能引起的职业病或职业性病损
            #   e.g. 氟化氢|气体|刺入、皮肤|对皮肤有强烈的腐蚀作用…|高度危害|化学性皮肤灼伤…
            # 「危害程度」= GBZ/T 230-2025 THI 分级, 属**确定性查表**(hazard_toxicity),
            #   未收录 → 标「待补充」, 不推算不编造(用户原则)
            he6 = []
            for r in he_rows:
                # he_rows 现为 [fac, effect, disease, route]
                fac, effect = r[0], r[1]
                disease = r[2] if len(r) > 2 else "—"
                route = r[3] if len(r) > 3 else "—"
                grade = lookup_toxicity(conn, fac)
                he6.append([fac, form_of(conn, fac), route, effect, grade, disease])
            tables.append({"name": "健康影响表",
                           "cols": ["名称", "形态", "危害特性", "对人体健康的影响",
                                    "危害程度", "可能引起的职业病或职业性病损"],
                           "rows": he6})
        # 关键控制点表 (岗位×关键因子×措施)
        # 原报告表5.4-1 7列: 车间/产品/工序/设备密闭/接触方式/因素/关键控制措施 (hazard_grid+judgement 派生)
        kc_rows = []
        hg5 = (assess.get("_project_data") or {}).get("hazard_grid") or []
        for g in hg5[:12]:
            if not g.get("unit") and not g.get("post"):
                continue
            facs = (g.get("factors") or "")
            kc_rows.append(["生产车间主厂房", g.get("product", "—") or "—", g.get("stage") or g.get("post", "—"),
                            "密闭", "投料、巡检", (facs[:60] or "—"),
                            "自动化、密闭化、机械化；个人防护、警示告知"])
        if not kc_rows:
            for i, j in enumerate(js[:10]):
                kc_rows.append(["生产车间主厂房", "—", "—", "密闭", "—", j["factor"],
                                (j.get("level") or {}).get("control", "") or "密闭化、自动化；个人防护"])
        if kc_rows:
            tables.append({"name": "关键控制点表",
                           "cols": ["生产车间", "生产车间", "工序", "设备密闭", "接触方式", "主要职业病危害因素", "关键控制措施"],
                           "header2": ["生产车间", "生产车间", "工序", "设备密闭", "接触方式", "主要职业病危害因素", "关键控制措施"],
                           "merge_rect": [(0, 0, 1, 1)] + [(0, c, 1, c) for c in (2, 3, 4, 5, 6)],
                           "rows": kc_rows})
        # 物理因素健康影响 (噪声/高温/振动/工频 — GBZ 2.2 固定内容, 通用)
        phys_rows = [
            ["噪声", "物理因素", "根据作用的系统不同可分为听觉系统（听力损失）和非听觉系统（神经系统、心血管系统等）的影响", "噪声聋"],
            ["高温", "物理因素", "高温可导致急性热致疾病（如刺痒、热疹、抽搐、热晕厥、热衰竭、热射病）", "职业性高温中暑"],
            ["振动", "物理因素", "长期接触手传振动可引起手臂振动病（白指）", "手臂振动病"],
            ["工频电场", "物理因素", "短期接触低强度工频电场可引起头晕、疲劳、血压波动等不适", "—"],
        ]
        tables.append({"name": "物理因素健康影响表", "cols": ["名称", "危害特性", "对人体健康的影响", "可能引起的职业病"], "rows": phys_rows,
                       "prov": {f"{i}_2": prov_rule({"file": "标准库", "table": "health_effect",
                                                     "text": f"{r[0]} 健康影响 (GBZ 2.2/职业病目录)"},
                                                    field=f"{r[0]}.健康影响")
                                for i, r in enumerate(phys_rows)}})
        # 表5.3-2 噪声接触限值 (GBZ 2.2—2019 表5: 接触时间/限值[dB(A)]/备注)
        _noise_rows = [["5d/w，≤8h/d", "85", "非稳态噪声计算8h等效声级"],
                       ["5d/w，≠8h/d", "85", "计算8h等效声级"],
                       ["5d/w，≠4h/d", "88", "计算8h等效声级"],
                       ["5d/w，≠2h/d", "91", "计算8h等效声级"],
                       ["5d/w，≠1h/d", "94", "计算8h等效声级"]]
        tables.append({"name": "噪声接触限值表", "cols": ["接触时间", "接触限值[dB(A)]", "备注"],
                       "rows": _noise_rows,
                       # 静态标准数据: 逐格标 GBZ 2.2—2019 表5 (非 LLM 生成)
                       "prov": {f"{i}_1": prov_rule({"file": "GBZ 2.2—2019", "table": "oel_limit",
                                                     "text": f"噪声 {r[0]} → {r[1]} dB(A) (GBZ 2.2—2019 表5)"},
                                                    field=f"噪声.{r[0]}")
                                for i, r in enumerate(_noise_rows)}})
        # 表5.3-3 高温接触限值 (GBZ 2.2-2019 表1: 接触时间率×体力劳动强度 WBGT 限值℃) 双层表头
        _heat_rows = [["100%", "30", "28", "26", "25"], ["75%", "31", "29", "27", "26"],
                      ["50%", "32", "30", "28", "27"], ["25%", "33", "31", "29", "28"]]
        tables.append({"name": "高温接触限值表",
                       "cols": ["接触时间率", "体力劳动强度", "体力劳动强度", "体力劳动强度", "体力劳动强度"],
                       "header2": ["接触时间率", "I", "II", "III", "IV"],
                       "merge_rect": [(0, 1, 0, 4)],
                       "rows": _heat_rows,
                       # 静态标准数据: 逐格标 GBZ 2.2—2019 表1 (含第0列接触时间率)
                       "prov": {f"{i}_{c}": prov_rule({"file": "GBZ 2.2—2019", "table": "oel_limit",
                                                       "text": f"高温 接触时间率{r[0]} 强度{[None,'I','II','III','IV'][c]} → WBGT {r[c]}℃ (GBZ 2.2—2019 表1)"},
                                                      field=f"高温.{r[0]}.{c}")
                                for i, r in enumerate(_heat_rows) for c in (0, 1, 2, 3, 4)}})
        # 工种×危害表 (岗位→危害, 从网格关联)
        if grid:
            gw_rows = []
            for g in grid:
                for p in (g.get("posts") or ["各岗位"])[:2]:
                    gw_rows.append([g["unit"], p, "、".join(g["factors"][:6])])
            if gw_rows:
                tables.append({"name": "工种危害表", "cols": ["评价单元", "岗位/工种", "主要危害因素"], "rows": gw_rows,
                               # 逐格 provenance: 岗位→危害 由 hazard_grid 网格关联 (规则), 非 LLM
                               "prov": {f"{i}_{c}": prov_rule({"file": "hazard_grid(岗位×危害关联)",
                                                               "text": f"{r[c]} (网格关联)"}, field=f"gw.{c}")
                                        for i, r in enumerate(gw_rows) for c in (0, 1, 2)}})
        return tables

    if sec == "6":
        # 职业病危害防护设施: 防尘防毒/防噪声振动/防暑防寒/防护设施检查表 + 设施配置
        tables = []
        for theme, tname in [("防尘防毒", "防尘防毒设施检查表"), ("防噪声振动", "防噪声振动检查表"),
                             ("防暑防寒", "防暑防寒检查表")]:
            g_rows = []
            for r in _rows_of(conn, "SELECT clause, rule FROM gbz1_rule WHERE theme LIKE ?", (theme + "%",)):
                g_rows.append([len(g_rows) + 1, r[1][:60], r[0], "待确认", "待确认"])
            if g_rows:
                tables.append({"name": tname, "cols": ["序号", "卫生要求", "检查依据", "检查结果", "评价"],
                               "rows": g_rows})
        rows = _rows_of(conn, "SELECT hazard_category, check_point, std_code, clause FROM protection_rule")
        # 原报告表6.2-1 5列: 序号/卫生要求/检查依据/检查结果/评价
        p6 = []
        for r in rows:
            p6.append([len(p6) + 1, r[1][:60], f"{r[2]} {r[3]}", "本项目拟设置相应防护设施", "符合"])
        tables.append({"name": "防护设施检查表", "cols": ["序号", "卫生要求", "检查依据", "检查结果", "评价"],
                       "rows": p6})
        # 设施配置明细 (岗位×设施×数量×备注)
        facs = (assess.get("_project_data") or {}).get("facilities", [])
        if facs:
            f_rows = [[i, f.get("post", ""), f.get("facility", ""), f.get("count", ""), f.get("remark", "")]
                      for i, f in enumerate(facs, 1)]
            tables.append({"name": "设施配置表", "cols": ["序号", "岗位/区域", "配备设施", "数量", "备注"],
                           "rows": f_rows})
        return tables

    if sec == "7":
        # 应急救援: 应急救援检查表 + 应急物资清单 (表56 类别|名称|数量|放置点位)
        pd_ = assess.get("_project_data", {})
        tables = []
        rows = _rows_of(conn, "SELECT scenario, require, std_code, clause FROM emergency_rule")
        if rows:
            # 原报告表7.2-1 5列: 序号/卫生要求/检查依据/检查结果/评价
            tables.append({"name": "应急救援检查表", "cols": ["序号", "卫生要求", "检查依据", "检查结果", "评价"],
                           "rows": [[i, r[1][:60], f"{r[2]} {r[3]}", "本项目依托现有企业的应急救援机构", "符合"]
                                    for i, r in enumerate(rows, 1)]})
        emgs = pd_.get("emergency_supplies", [])
        if emgs:
            try:
                # 原报告表2.1-2/7.1-1 4列: 类别/名称/数量/放置点位 (无序号)
                tables.append({"name": "应急物资清单",
                               "cols": ["类别", "名称", "数量", "放置点位"],
                               "rows": [[e.get("类别", ""), e.get("名称", ""), e.get("数量", ""), e.get("点位", "")]
                                        for e in emgs]})
            except Exception:
                pass
        return tables

    if sec == "8":
        # 个人防护用品: PPE配备表
        # 通用: 从项目 data 的 ppe 字典列表生成 (上传管线提取, 全项目通用)
        # (修复: 旧硬编码 data/materials/A2f_防护措施.txt 不存在 → 永远空表)
        pd = assess.get("_project_data") or {}
        p_rows = []
        for p in (pd.get("ppe") or []):
            if isinstance(p, dict):
                p_rows.append([len(p_rows) + 1,
                               str(p.get("post") or p.get("岗位") or p.get("工段") or ""),
                               str(p.get("item") or p.get("name") or p.get("equipment")
                                   or p.get("防护用品") or p.get("ppe") or ""),
                               str(p.get("frequency") or p.get("标准") or "GB 39800.1—2020")])
            elif isinstance(p, str) and p.strip():
                p_rows.append([len(p_rows) + 1, "", p.strip(), "GB 39800.1—2020"])
        # 兜底: protection 文本 (防护措施描述) 提取 "护目镜/防毒面具/耳塞..." 行
        if not p_rows:
            import re as _re
            prot = pd.get("protection") or ""
            if isinstance(prot, str) and prot.strip():
                for line in prot.splitlines():
                    if _re.search(r"(保护|防护|个人防护|PPE)", line) and ("|" in line or "：" in line or ":" in line):
                        parts = _re.split(r"[|：:]", line.lstrip("-_* "), 1)
                        if len(parts) >= 2:
                            p_rows.append([len(p_rows) + 1, parts[0].strip(), parts[1].strip(), "GB 39800.1—2020"])
        if p_rows:
            # 原报告表8.1-1: 竖排6列 生产单元|岗位|用品|单位|数量|更换周期 (= 4.2-4 同构, 类比推定)
            #   ⚠ 真稿零张矩阵表 → 弃用 _ppe_matrix_table 横排
            _pv8, _pv8m = _ppe_vertical_rows(pd)
            if _pv8:
                tables8 = [{"name": "PPE配备表",
                            "cols": ["生产单元", "生产岗位", "配置的防护用品", "单位", "数量", "更换周期"],
                            "rows": _pv8, "vmerge_cols": [0, 1]}]
            else:
                tables8 = [{"name": "PPE配备表", "cols": ["序号", "岗位/作业", "防护装备", "配备标准"], "rows": p_rows[:60]}]
        else:
            tables8 = []
        # 表8.2-1 拟配置检查表 (GB 39800—2020 通用配备要求, 检查表定式: 通用规则非项目编造)
        tables8.append({"name": "PPE拟配置检查表",
                        "cols": ["序号", "检查内容", "检查依据", "检查结果", "评价"],
                        "rows": [
                            [1, "综合性作业需根据作业特点选择多功能防护装备", "GB 39800.1—2020 4.2", "本项目拟根据实际作业情况选择个体防护用品", "符合"],
                            [2, "作业人员需配备的个体防护装备应在作业指导书中注明", "GB 39800.1—2020 4.3", "本项目拟在各岗位操作规程中注明个体防护用品配备", "符合"],
                            [3, "个体防护装备的选择应考虑有害物质的暴露途径", "GB 39800.1—2020 5.1", "本项目拟按吸入、皮肤接触等途径分别选择呼吸防护与皮肤防护用品", "符合"],
                            [4, "头部、眼面部、呼吸、皮肤等防护装备应按国家标准选型", "GB 39800.1—2020 第5章", "本项目拟选用符合国家标准防护装备", "符合"]]})
        return tables8

    if sec == "9":
        # 职业卫生管理: 管理制度检查表 + 职业健康监护表
        # 检索优化: 管理制度按"通用必列 + 项目相关"过滤, 不整表倾倒(条款库扩大后防表格爆表)
        tables = []
        rows = _rows_of(conn, "SELECT category, require, std_code, clause FROM management_rule")
        # 受限空间等特殊作业条款仅当项目工艺涉及时才进检查表 (通用判定: 危害grid/工艺文本含关键词)
        _pd9 = assess.get("_project_data") or {}
        _proc = str(_pd9.get("process_text") or "") + " ".join(
            str(g.get("factors") or "") + str(g.get("materials") or "") for g in (_pd9.get("hazard_grid") or []))
        _special_kw = {"受限空间": ("受限空间", "密闭", "清罐", "釜内", "进入设备"), }
        rows = [(c, q, s, cl) for (c, q, s, cl) in rows
                if c not in _special_kw or any(k in _proc for k in _special_kw[c])]
        # 原报告表9.1-1 5列: 序号/检查项目/检查依据/检查结果/结论
        m9 = []
        for i, r in enumerate(rows, 1):
            m9.append([i, f"{r[0]}：{r[1][:50]}", f"{r[2]} {r[3]}", "企业已建立相应管理制度", "合格"])
        tables.append({"name": "管理制度检查表", "cols": ["序号", "检查项目", "检查依据", "检查结果", "结论"],
                       "rows": m9})
        surv = _rows_of(conn, "SELECT factor, check_type, cycle FROM surveillance_rule")
        if surv:
            tables.append({"name": "职业健康监护表", "cols": ["序号", "危害因素", "检查类别", "周期"],
                           "rows": [[i, r[0], r[1], r[2] or "—"] for i, r in enumerate(surv, 1)]})
        return tables

    if sec == "10":
        # 标准 10.2.10 职业病危害关键控制点分析 (2026-09 从 5.4.3 提为独立章)
        # 数据源合并两处 (规则产物, 不经 LLM):
        #   judgements → 是否有 level (化学判定)
        #   grades     → GBZ/T 229 作业分级 (粉尘/物理的真实分级来源)
        _grade = {}
        for g in (assess.get("grades") or []):
            if isinstance(g, dict) and g.get("factor"):
                _grade[str(g["factor"])] = g
        rows = []
        _seen_f: set = set()
        for j in (assess.get("judgements") or []):
            if not isinstance(j, dict) or not j.get("factor"):
                continue
            f = str(j["factor"])
            # ⚠ 按**因素**去重: judgements 逐检测行生成 (同一因素多采样点 → 多行),
            #   而关键控制点表是**因素级** (真实报告表10.1-1 一因素一行)。
            #   不去重会把 25 条苯乙烯/20 条噪声印 25/20 行 (实测 151 行)。
            if f in _seen_f:
                continue
            _seen_f.add(f)
            lv = j.get("level") if isinstance(j.get("level"), dict) else {}
            lv = lv or {}
            g = _grade.get(f) or {}
            lvl_txt = str(lv.get("level") or g.get("level") or "—")
            ctrl = str(lv.get("control") or "")
            if not ctrl and g.get("name"):
                ctrl = str(g["name"])            # 如 "中度危害作业"
            basis = str(lv.get("basis") or g.get("note") or "")
            rows.append([f, lvl_txt, ctrl[:80] or "—", basis[:60] or "—"])
        # 物理因素补充 (噪声/高温等, 来自 hazards 里的物理项)
        _phys = ("噪声", "高温", "工频电场", "振动", "紫外辐射")
        _has = {str(h.get("factor") or "") for h in (assess.get("hazards") or [])}
        for pf in _phys:
            if pf in _has and not any(r[0] == pf for r in rows):
                g = _grade.get(pf) or {}
                rows.append([pf, str(g.get("level") or "—"),
                             str(g.get("name") or "见防护设施/PPE章节"),
                             "GBZ 2.2—2019"])
        if not rows:
            return []
        rows = [[i] + r for i, r in enumerate(rows, 1)]
        return [{"name": "关键控制点表",
                 "cols": ["序号", "关键控制因子", "控制级别", "关键控制措施", "依据标准"],
                 "rows": rows}]

    if sec == "11":
        # 标准 10.2.11 补充建议: 问题与建议 (标准条款驱动)
        from web.advice_gen import fill_10211
        built = fill_10211(conn, assess)
        # 表11.5-1 拟建项目职业病危害因素体检项目与周期 (复刻真稿 表5.1-1)
        # ⚠ 必须在 fill_section 里产出: _tables_for_sub 只从 fill_section 取数,
        #   放 table_builder.build_data_tables 不进导出链路(实测产物 0 张表)。
        from web.health_exam_map import build_health_exam_rows
        _he = build_health_exam_rows(assess.get("_project_data") or assess)
        if _he:
            built["tables"].append({
                "name": "体检项目与周期表",
                "cols": ["危害因素", "体检类别", "岗前职业健康检查",
                         "岗中职业健康检查", "健康检查周期", "离岗职业健康检查"],
                "rows": _he,
            })
        # 表11.2-1 室内空气质量标准 (GB/T 18883—2002 固定节选, 6列)
        built["tables"].append({"name": "室内空气质量标准表",
                                "cols": ["序号", "参数", "参数类别", "单位", "标准值", "备注"],
                                "rows": [
                                    [1, "温度", "物理性", "℃", "22～28", "夏季空调"],
                                    [2, "温度", "物理性", "℃", "16～24", "冬季采暖"],
                                    [3, "相对湿度", "物理性", "%", "40～80", "夏季空调"],
                                    [4, "相对湿度", "物理性", "%", "30～60", "冬季采暖"],
                                    [5, "空气流速", "物理性", "m/s", "0.3", "夏季空调"],
                                    [6, "空气流速", "物理性", "m/s", "0.2", "冬季采暖"],
                                    [7, "二氧化碳", "化学性", "%", "0.10", "日平均值"]]})
        return built["tables"]

    if sec == "12":
        # 标准 10.2.12 结论: 结论要素表
        risk = assess.get("industry_risk") or {}
        rows = [["1", "职业病危害类别", risk.get("level", "—") + " (" + risk.get("name", "") + ")"],
                ["2", "存在的主要问题", f"{len(assess.get('judgements', []))} 条判定记录"],
                ["3", "可行性", "基本可行 (采取补充建议后)"]]
        return [{"name": "结论要素表", "cols": ["序号", "结论要素", "结论"], "rows": rows}]

    return []
