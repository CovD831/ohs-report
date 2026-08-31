"""项目评估管线 — 识别引擎 + 判定引擎 + 9知识库全链 (职业病危害预评价核心)

输入 (项目数据, 用户提供):
  name        项目名称
  industry    行业码 4位 (GB/T 4754 中类码, 如 261) — 注意: 不带门类字母C
  equipment   [设备名称, ...]        设备清单
  processes   [{unit, process}, ...] 生产单元/工序 (可选, 匹配工作单元规则)
  detections  [{factor, ctwa, cste, cme, peak, value, oel_type, rate, labor,
                bio_value, bio_unit, indicator}...]  检测数据 (可选)

输出 (报告 10.2 各章节数据):
  10.2.1 总论        project_info (基础)
  10.2.3.1 工程概况   industry_chain (行业分类全层级) + industry_risk (风险分类)
  10.2.5.1 危害识别   hazards (设备/工序→危害→OEL/方法/分类/危化品)
  10.2.5.2 健康效应   occupational_diseases (危害→可能职业病)
  10.2.5.3 危害程度   judgements (判定) + grades (作业分级 229) + level_summary
  10.2.8  PPE       ppe_recommendations (危害类别→防护装备)
  10.2.9  健康监护   surveillance (危害→检查项目/周期)
  10.2.3.7 建筑卫生学 illumination_check (车间→照度标准值)
  10.2.3.8 辅助用室   gbz1_auxiliary (卫生特征→浴室/更衣室规则)
  10.2.12 结论       final_conclusion (核心判定摘要)

链路: 行业码→行业链+风险 | 设备→物料→OEL物质→危害→职业病
      | 危害→监护规则/PPE | 危害×检测→判定+作业分级 | 车间→照度检查
用法: python3 -m knowledge.project_assess
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402
from knowledge.identify_demo import split_materials  # noqa: E402
from knowledge.judge_cli import (judge_chemical, judge_physical, judge_bio,  # noqa: E402
                                 get_oel, level_of)
from knowledge.grade_engine import grade_chemical, grade_dust, grade_heat  # noqa: E402

# 物理因素→作业分级函数映射 (注意: 物理因素用 229 无单独函数, 高温/噪声走特殊)
PHYSICAL_FACTORS = ("噪声", "高温", "手传振动", "工频电场", "微波辐射",
                    "超高频辐射", "高频电磁场", "紫外辐射")


def merge_process_materials(conn, hazards: dict, process_text: str) -> dict:
    """工艺描述层: 段落文本 → 物质名 → 合并入 hazards (source=工艺描述)
    无OEL的物质 (三羟甲基丙烷/聚乙二醇...) 保持 needs_test=False 仅提示"""
    from knowledge.process_material_extractor import extract_from_text
    names = extract_from_text(conn, process_text)
    for n in names:
        h = hazards.setdefault(n, {"sources": set(), "via": set()})
        h["sources"].add("工艺描述")
    return hazards


def _auto_factor(conn, w: str) -> str:
    """物料名→危害因素: 通用管线, 直接查 oel_limit/hazard_factor (精确/包含), fallback material_dictionary别名.

    不依赖手工映射表 — 任何物料名词走这个自动管线:
      1. oel_limit.factor_name 精确 (邻苯二甲酸酐→邻苯二甲酸酐)
      2. oel_limit 包含 (物料名是因子子串)
      3. hazard_factor.name 精确
      4. material_dictionary 别名 (50%乙二醇→乙二醇)
    顺酐/苯酐等物料名直接命中 oel_limit(马来酸酐/邻苯二甲酸酐), 无需人工补词典。
    """
    if not w:
        return ""
    w = str(w).strip().replace(" ", "")
    if not w:
        return ""
    # 1. oel_limit 精确
    r = conn.execute("SELECT factor_name FROM oel_limit WHERE factor_name=? LIMIT 1", (w,)).fetchone()
    if r:
        return r[0]
    # 2. oel_limit 包含 (物料名是因子子串)
    r = conn.execute("SELECT factor_name FROM oel_limit WHERE factor_name LIKE ? LIMIT 1", ("%" + w + "%",)).fetchone()
    if r:
        return r[0]
    # 3. hazard_factor 精确
    r = conn.execute("SELECT name FROM hazard_factor WHERE name=? LIMIT 1", (w,)).fetchone()
    if r:
        return r[0]
    # 4. material_dictionary 别名
    r = conn.execute("SELECT oel_factor FROM material_dictionary WHERE material=? LIMIT 1", (w,)).fetchone()
    if r and r[0]:
        return r[0]
    return ""


def identify_hazards(conn, equipment: list[str], processes: list[dict] | None = None,
                     process_text: str = "", materials: list | None = None) -> list[dict]:
    """识别引擎: 项目物料→危害主源 + 设备→物料→OEL + 工序→规则危害 (来源链保留)

    修复: 物料→危害判定改用 _auto_factor 通用管线 (物料名词直接查 oel_limit/hazard_factor),
    不依赖手工 material_dictionary 映射, 任何行业/项目物料自动匹配。
    """
    hazards = {}   # factor_name -> {sources:set, via:set}
    # 0) 项目物料归一化 (原辅料表, 本项目权威物料清单)
    proj_mats = []
    for m in (materials or []):
        if isinstance(m, dict):
            nm = str(m.get("name") or m.get("物料名称") or "").strip()
        else:
            nm = str(m).split("|")[0].strip()
        nm = nm.replace(" ", "")
        if nm and nm not in proj_mats:
            proj_mats.append(nm)
    # 0.5) 项目物料→危害 (主源, _auto_factor 通用管线: 苯乙烯/邻苯二甲酸酐/马来酸酐/乙二醇自动命中)
    for w in proj_mats:
        f = _auto_factor(conn, w)
        if f:
            h = hazards.setdefault(f, {"sources": set(), "via": set()})
            h["sources"].add("原辅料")
            h["via"].add(w)
    # 1) 设备→物料→危害 (equipment_material 设备查物料, _auto_factor 自动匹配; 补充源)
    # 通用性: equipment_material 是行业知识库(长兴表17/压克力线), 设备关联的物料必须属于本项目材料清单
    # (proj_mats), 否则是其他行业知识库噪声 —— 如长兴"乙二醇/环己烷/甲苯/甲醇"污染新泰无机盐项目。
    # 任何项目设备碰到的物料, 只有在本项目原辅料清单里才算真实危害 (准确优先, 宁缺失勿污染)。
    for eq in equipment:
        mats = conn.execute(
            "SELECT DISTINCT material FROM equipment_material WHERE equipment LIKE ?",
            (f"%{eq}%",)).fetchall()
        for (m,) in mats:
            for word in split_materials(m):
                if word not in proj_mats:
                    continue
                f = _auto_factor(conn, word)
                if f:
                    h = hazards.setdefault(f, {"sources": set(), "via": set()})
                    h["sources"].add(f"设备[{eq}]")
                    h["via"].add(word)
    # 2) 工序→规则 (work_unit_rule: 生产单元/工序 → 危害因素)
    if processes:
        for pu in processes:
            unit, proc = pu.get("unit", ""), pu.get("process", "")
            rows = conn.execute(
                "SELECT unit, process, factors FROM work_unit_rule "
                "WHERE (unit LIKE ? OR ?='') AND (process LIKE ? OR ?='')",
                (f"%{unit}%", unit, f"%{proc}%", proc)).fetchall()
            for u, p, fx in rows:
                for f in fx.split(";"):
                    f = f.strip()
                    if f:
                        h = hazards.setdefault(f, {"sources": set(), "via": set()})
                        h["sources"].add(f"工序[{u}/{p}]")
    # 2.5) 工艺描述层: 段落文本 → 物质 (碳酸钠/三羟甲基丙烷/1,6-己二醇...)
    if process_text:
        merge_process_materials(conn, hazards, process_text)
    # 3) 附加 OEL 限值/方法/生物限值/目录分类/危化品CAS标注/监护/职业病
    out = []
    for f, info in sorted(hazards.items()):
        # --- OEL ---
        oels = get_oel(conn, f)
        # --- 检测方法 ---
        method = conn.execute(
            "SELECT standard_code FROM method_catalog WHERE factor LIKE ? LIMIT 1",
            (f"%{f}%",)).fetchone()
        # --- BEI ---
        bio = conn.execute(
            "SELECT COUNT(*) FROM bio_limit WHERE factor_name=?", (f,)).fetchone()[0]
        # --- 分类标签 ---
        cat_row = conn.execute(
            "SELECT category, name FROM hazard_factor WHERE name=? LIMIT 1", (f,)).fetchone()
        if not cat_row:
            alias = conn.execute(
                "SELECT catalog_name FROM hazard_factor_alias WHERE oel_name=? "
                "AND match_type IN ('exact','core_eq') LIMIT 1", (f,)).fetchone()
            if alias:
                cat_row = conn.execute(
                    "SELECT category, name FROM hazard_factor WHERE name=? LIMIT 1",
                    (alias[0],)).fetchone()
        # --- 危化品 CAS ---
        cas = conn.execute(
            "SELECT cas FROM oel_limit WHERE factor_name=? AND cas IS NOT NULL LIMIT 1",
            (f,)).fetchone()
        hazchem = None
        if cas and cas[0]:
            h = conn.execute(
                "SELECT name, note, is_toxic FROM hazchem_item WHERE cas=? LIMIT 1",
                (cas[0],)).fetchone()
            if h:
                hazchem = {"name": h[0], "note": h[1],
                           "is_toxic": bool(h[2])}
        # --- 监护规则 (surveillance_rule) ---
        surv = conn.execute(
            "SELECT check_type, cycle, must_items, target_disease FROM surveillance_rule "
            "WHERE factor LIKE ? AND check_type='在岗期间' LIMIT 1", (f"%{f}%",)).fetchone()
        # --- 职业病 (occupational_disease: 危害名称→疾病, 用包含匹配) ---
        diseases = []
        for r in conn.execute(
                "SELECT category, name FROM occupational_disease WHERE name LIKE ? OR name LIKE ? LIMIT 3",
                (f"%{f}%", f"%{f.split('及其')[0]}%")):
            diseases.append(r[0])
        out.append({
            "factor": f,
            "sources": sorted(info["sources"]),
            "via_materials": sorted(info["via"]),
            "oel": [{"type": r["oel_type"], "value": r["value"], "unit": r["unit"]}
                    for r in oels],
            "method": method[0] if method else None,
            "has_bio": bio > 0,
            "needs_test": bool(oels),  # 有OEL需检测(mg/m3等数值对比)
            "catalog": {"category": cat_row[0], "name": cat_row[1]} if cat_row else None,
            "hazchem": hazchem,
            "surveillance": {"cycle": surv[0], "must_items": surv[1],
                             "target_disease": surv[2]} if surv else None,
            "diseases": diseases,
        })
    return out


def industry_chain(conn, industry_code: str) -> dict | None:
    """行业链: 4位中类码 → 门类/大类/中类/小类 全层级 (10.2.3.1)
    industry_code: '261' (中类码) / 'C261' / 'C265合成材料制造'(含文字) — 提取纯代码"""
    if not industry_code:
        return None
    # 提取纯代码: 'C265合成材料制造'→'265', '441电力'→'441', 'C261'→'261'
    import re
    m = re.search(r"(\d{3,4})", str(industry_code))
    if m:
        industry_code = m.group(1)
    else:
        industry_code = str(industry_code).strip()
    # 中类: 直接查 3 位码 (261)
    mid = conn.execute(
        "SELECT code, name FROM industry_class WHERE code=? AND level='中类' LIMIT 1",
        (industry_code,)).fetchone()
    if not mid:
        # 4位小类码 (如2651): 查小类, 用其名称 (chain 显示完整小类, 而非截断误映射)
        if re.match(r"^\d{4}$", str(industry_code)):
            sm = conn.execute(
                "SELECT code, name FROM industry_class WHERE code=? AND level='小类' LIMIT 1",
                (industry_code,)).fetchone()
            if sm:
                mid = (sm[0], sm[1])
                big = conn.execute(
                    "SELECT code, name FROM industry_class WHERE code=? AND level='大类' LIMIT 1",
                    (industry_code[:2],)).fetchone()
        # 兼容: 用户给了 C261 → 取后3位 (仅当输入是 '<字母>3位码' 形式)
        # (修复: 4位码如'2651'是完整小类, 不能截成后3位'651' → 误映射软件开发!
        #  2651 初级形态塑料及合成树脂制造 被 industry_chain 映射到 651 软件开发)
        if not mid and re.match(r"^[A-Za-z]\d{3}$", str(industry_code)):
            mid = conn.execute(
                "SELECT code, name FROM industry_class WHERE code=? AND level='中类' LIMIT 1",
                (industry_code[-3:],)).fetchone()
            industry_code = industry_code[-3:]
        if not mid:
            return None
    # 大类: 中类前2位 (261 → 26)
    big = conn.execute(
        "SELECT code, name FROM industry_class WHERE code=? AND level='大类' LIMIT 1",
        (industry_code[:2],)).fetchone()
    if not big and re.match(r"^\d{4}$", str(industry_code)):
        big = conn.execute(
            "SELECT code, name FROM industry_class WHERE code=? AND level='大类' LIMIT 1",
            (industry_code[:2],)).fetchone()
    # 门类: 大类范围 (26 → C 13-43)
    gate_code = None
    for gate, (lo, hi) in GATE_RANGES.items():
        if lo <= int(industry_code[:2]) <= hi:
            gate_code = gate
            break
    if gate_code:
        gate = conn.execute(
            "SELECT code, name FROM industry_class WHERE code=? AND level='门类' LIMIT 1",
            (gate_code,)).fetchone()
    else:
        gate = None
    return {
        "gate": gate.lastrowid if False else ({"code": gate[0], "name": gate[1]} if gate else None),
        "big": {"code": big[0], "name": big[1]} if big else None,
        "mid": {"code": mid[0], "name": mid[1]},
        "full": " > ".join(filter(None, [
            gate[1] if gate else None,
            big[1] if big else None,
            mid[1],
        ])),
    }


# GB/T 4754 门类范围 (按大类码区间)
GATE_RANGES = {
    "A": (1, 5), "B": (6, 12), "C": (13, 43), "D": (44, 46),
    "E": (47, 50), "F": (51, 52), "G": (53, 60), "H": (61, 62),
    "I": (63, 65), "J": (66, 69), "K": (70, 70), "L": (71, 72),
    "M": (73, 75), "N": (76, 79), "O": (80, 82), "P": (83, 84),
    "Q": (85, 86), "R": (87, 88), "S": (89, 90), "T": (91, 96),
}


def ppe_for_hazards(conn, hazards: list[dict]) -> list[dict]:
    """PPE 建议: 危害类别→防护装备 (GB 39800 9类, 规则映射+人工把关)
    规则 (危害→装备, 按 GB 39800.1 4.3 危害评估逻辑):
      粉尘→HX呼吸防护(op) + YM眼面 | 化学物→FZ化学防护服 + HX + YM
      噪声→TL听力 | 高温→FZ隔热服 | 振动→SF手部 | 电离辐射→SF电离防护手套
      生物→HX + FZ | 坠落→ZL | 物理(电场/微波)→YM + FZ
    """
    # 规则 (危害→装备, 按 GB 39800.1 4.3 危害评估逻辑):
    # 条目用 精确 item_name (化学→化学防护服FZ-07, 呼吸→防毒面具, 眼面→职业眼面YM-04)
    rules = {
        "粉尘": [("HX", "动力送风过滤式呼吸器"), ("YM", "职业眼面部防护具")],
        "噪声": [("TL", "耳塞")],
        "高温": [("FZ", "隔热服"), ("YM", "职业眼面部防护具")],
        "振动": [("SF", "机械危害防护手套")],
        "化学": [("FZ", "化学防护服"), ("HX", "长管呼吸器"), ("YM", "职业眼面部防护具")],
        "生物": [("HX", "动力送风过滤式呼吸器"), ("FZ", "化学防护服")],
        "物理": [("YM", "激光防护镜"), ("FZ", "防静电服")],
    }
    out = []
    for h in hazards:
        cat = (h.get("catalog") or {}).get("category", "")
        keys = []
        if "粉尘" in cat:
            keys.append("粉尘")
        if "化学" in cat or h.get("hazchem"):
            keys.append("化学")
        if "物理" in cat and not any(k in h["factor"] for k in ("噪声", "高温", "振动")):
            keys.append("物理")
        if "噪声" in h["factor"]:
            keys.append("噪声")
        if "高温" in h["factor"]:
            keys.append("高温")
        if "手传振动" in h["factor"]:
            keys.append("振动")
        if "生物" in cat:
            keys.append("生物")
        if not keys:
            keys = ["化学"]  # 默认保守
        items = []
        seen = set()
        for k in keys:
            for code, iname in rules.get(k, ()):
                item = conn.execute(
                    "SELECT item_name, std_code FROM ppe_item "
                    "WHERE cat_code=? AND item_name=? LIMIT 1", (code, iname)).fetchone()
                if not item:
                    item = conn.execute(
                        "SELECT item_name, std_code FROM ppe_item "
                        "WHERE cat_code=? LIMIT 1", (code,)).fetchone()
                if item and item[0] not in seen:
                    seen.add(item[0])
                    items.append({"class": code, "item": item[0], "std": item[1]})
        out.append({"factor": h["factor"], "ppe": items})
    return out


def assess_project(conn, project: dict) -> dict:
    """项目评估: 识别 + 判定 + 分级 + 监护 + PPE 全链"""
    # 1) 行业风险分类 + 行业链
    risk = None
    ind = project.get("industry")
    if ind:
        # 从"441 电力、热力生产和供应业"/"C384"/"C3841"提取纯代码, 兼容 risk_category(C261/D44格式)
        import re
        m = re.search(r"([A-Z]?\d{2,4})", str(ind))
        pure = m.group(1) if m else str(ind).strip()
        candidates = {str(ind), pure}
        # 中类码: 3位取本身, 4位取前3位(C3841→C384), 2位(大类)保留
        m4 = re.search(r"([A-Z]?)(\d{3,4})", pure)
        if m4:
            prefix, digits = m4.group(1), m4.group(2)
            code3 = digits[:3]  # 取前3位 (C3841→384)
            candidates.add("C" + code3)
            candidates.add(code3)
            candidates.add(prefix + digits[:2])  # 大类2位 (D44 或 44)
            candidates.add("D" + digits[:2])
        # 去掉纯文字候选
        candidates = {c for c in candidates if re.search(r"\d", c)}
        for cand in candidates:
            r = conn.execute(
                "SELECT industry_code, industry_name, risk_level FROM risk_category "
                "WHERE industry_code=?", (cand,)).fetchone()
            if r:
                risk = {"code": r[0], "name": r[1], "level": r[2]}
                break
        # 精确匹配: 用行业名关键词在 risk_category 里找更具体的 (焚烧发电→火力发电)
        if risk and ind:
            # 关键词→risk_category 行的匹配 (如"生活垃圾焚烧发电"→"火力发电"严重)
            kw_rows = conn.execute(
                "SELECT industry_code, industry_name, risk_level FROM risk_category "
                "WHERE industry_name LIKE ? OR industry_name LIKE ? OR industry_name LIKE ?",
                ("%" + str(ind)[:6] + "%", "%发电%", "%电力生产%")).fetchall()
            if kw_rows and ("发电" in str(ind) or "电力生产" in str(ind)):
                # 发电/电力生产应取"严重"(火力/热电联产/生物质能)
                for kr in kw_rows:
                    if "严重" in kr[2]:
                        risk = {"code": kr[0], "name": kr[1], "level": kr[2]}
                        break
        # 工艺特征兜底: 垃圾/焚烧发电 → 生物质能发电(严重) (行业代码441太泛化, 按工艺归"严重")
        if risk and risk["level"] != "严重":
            eqs = project.get("equipment") or []
            eqs_txt = " ".join(str(e) for e in eqs)
            if ("垃圾" in str(ind) or "焚烧" in eqs_txt or "垃圾" in eqs_txt
                    or "余热锅炉" in eqs_txt or "汽轮发电机" in eqs_txt):
                r2 = conn.execute(
                    "SELECT industry_code, industry_name, risk_level FROM risk_category "
                    "WHERE industry_name LIKE '%生物质能发电%' AND risk_level='严重'").fetchone()
                if r2:
                    risk = {"code": r2[0], "name": r2[1], "level": r2[2]}
    chain = industry_chain(conn, ind) if ind else None
    # 2) 危害识别
    hazards = identify_hazards(conn, project.get("equipment", []),
                               project.get("processes"),
                               project.get("process_text", ""),
                               project.get("materials", []))
    # 3) 判定 (检测数据)
    judgements = []
    for d in project.get("detections", []):
        f = d["factor"]
        if d.get("bio_value") is not None:
            r = judge_bio(conn, f, d.get("indicator"), d["bio_value"], d.get("bio_unit", ""))
        elif any(k in f for k in PHYSICAL_FACTORS):
            r = judge_physical(conn, f, d.get("value"), oel_type=d.get("oel_type"),
                               rate=d.get("rate", "100%"), labor=d.get("labor", "Ⅰ"))
        else:
            r = judge_chemical(conn, f, d.get("ctwa"), d.get("cste"), d.get("cme"),
                               d.get("peak"))
        judgements.append({"factor": f, **r})
    # 4) 作业分级 (GBZ/T 229)
    grades = []
    for d in project.get("detections", []):
        f = d["factor"]
        labor = d.get("labor", "Ⅱ")
        if "高温" in f:
            g = grade_heat(labor, d.get("rate_pct", 100), d.get("wbgt", 30))
        elif d.get("sio2"):
            g = grade_dust(d["sio2"], d.get("btw", 0.5), labor)
        elif d.get("wd") or d.get("b"):
            g = grade_chemical(d.get("wd", "中度危害"), d.get("b", 0.5), labor)
        else:
            # 化学物默认: 用检出浓度/限值 比例 + 中度危害
            b = None
            if d.get("ctwa") is not None:
                try:
                    ctwa = float(d["ctwa"])  # ctwa可能是字符串(提取数据), 转float
                except (TypeError, ValueError):
                    ctwa = None
                oel = get_oel(conn, f)
                if oel and ctwa is not None:
                    b = ctwa / float(oel[0]["value"]) if float(oel[0]["value"]) > 0 else None
            if b is not None:
                g = grade_chemical(d.get("wd", "中度危害"), b, labor)
            else:
                g = None
        if g:
            grades.append({"factor": f, **g})
    # 5) PPE 建议
    ppe = ppe_for_hazards(conn, hazards)
    # 6) 接触水平汇总
    levels = {}
    for j in judgements:
        if j.get("level") and isinstance(j["level"], dict):
            lv = j["level"]["level"]
            levels.setdefault(lv, []).append(j["factor"])
    # 危害因素源头过滤: 只保留能回溯项目数据的因子 (物料精确/检测/grid/通用物理)
    # (修复: v10/v11 报告'硫酸二甲酯/苯醌/石蜡烟' — assess从行业模板/检查表带入, 项目data无此物料)
    _proj_mats = set()
    for m in (project.get("materials") or []):
        nm = (str(m.get("name") if isinstance(m, dict) else m)).split("|")[0].strip()
        if nm:
            _proj_mats.add(nm)
    _grid_factors = set()
    for g in (project.get("hazard_grid") or []):
        if isinstance(g, dict):
            for f in str(g.get("factors") or "").replace(" ", "").split("、"):
                if f and len(f) >= 2:
                    _grid_factors.add(f)
    _det_factors = set(str(d.get("factor") or "") for d in (project.get("detections") or []))
    _keep_common = {"噪声", "高温", "局部振动", "工频电场", "紫外辐射", "矽尘", "电焊烟尘",
                    "其他粉尘", "粉尘", "噪声(等效声级)", "WBGT", "电焊弧光", "臭氧",
                    "氮氧化物", "锰及其无机化合物", "三氧化铬", "金属镍与难溶性镍化合物"}

    def _real_factor(f):
        if not f:
            return False
        return (f in _det_factors or f in _grid_factors or f in _keep_common
                or f in _proj_mats)

    hazards = [h for h in hazards if isinstance(h, dict) and _real_factor(h.get("factor", ""))]
    # 去重 (同名因子合并)
    _seen_hz = {}
    for h in hazards:
        _seen_hz.setdefault(h["factor"], h)
    hazards = list(_seen_hz.values())

    return {
        "project": project.get("name", ""),
        "_project_data": project,  # 原文输入 (设备/检测/工艺) 供报告表格全量
        "industry_risk": risk,
        "industry_chain": chain,
        "hazards": hazards,
        "judgements": judgements,
        "grades": grades,
        "ppe": ppe,
        "level_summary": levels,
    }


def main():
    conn = connect()
    # 演示项目: 长兴特殊材料(苏州) 光固化涂料材料项目 (261, 真实设备+检测表25/26)
    project = {
        "name": "长兴特殊材料年产27080吨高性能光固化涂料材料项目",
        "industry": "261",
        "equipment": ["酯化釜", "纯化槽", "洗涤塔", "溶剂回收槽", "中和真空槽",
                      "酯化第一冷凝器", "真空除沫器", "洗釜泵", "油相溶剂泵"],
        "detections": [
            {"factor": "甲苯", "ctwa": 30, "cste": 95},
            {"factor": "环己烷", "ctwa": 0.3, "cste": None, "peak": 4.0},
        ],
    }
    result = assess_project(conn, project)
    print("=" * 60)
    print(f"📋 {result['project']}")
    if result["industry_chain"]:
        c = result["industry_chain"]
        print(f"  行业链: {c['full'] or '未定位'} (中类 {c['mid']['code']} {c['mid']['name']})")
    if result["industry_risk"]:
        r = result["industry_risk"]
        print(f"  风险分类: **{r['level']}** ({r['name']})")
    print("=" * 60)
    print(f"\n🔍 危害识别 ({len(result['hazards'])} 项):")
    for h in result["hazards"]:
        method = f" | 方法: {h['method']}" if h["method"] else " | 方法: 无GBZ/T300"
        bio = " | BEI:有" if h["has_bio"] else ""
        cat = f" [目录:{h['catalog']['category']}]" if h.get("catalog") else " [目录未收录]"
        hz = ""
        if h.get("hazchem"):
            hz = f" ⚠️危化品[{h['hazchem']['name']}]" + ("·剧毒" if h["hazchem"]["is_toxic"] else "")
        surv = " | 监护:有" if h.get("surveillance") else ""
        dis = f" | 职业病:{'、'.join(h['diseases'][:2])}" if h["diseases"] else ""
        print(f"  🔴 {h['factor']}{cat}{hz}{surv}{dis} [{'需检测' if h['needs_test'] else '定性'}]"
              f" ← {'; '.join(h['sources'][:3])}{method}{bio}")
    print(f"\n📊 判定 ({len(result['judgements'])} 条):")
    for j in result["judgements"]:
        status = "✅合格" if j.get("pass") else ("❌不合格" if j.get("pass") is False else "⚠️待测")
        lv = f" 级别:{j['level']['level']}" if isinstance(j.get("level"), dict) else ""
        print(f"  {j['factor']}: {status}{lv}")
        for c in j.get("checks", []):
            mark = "✅" if c["pass"] else "❌"
            print(f"    {mark} {c['rule']}: {c['value']} vs {c['limit']}")
    if result["grades"]:
        print(f"\n📈 作业分级 (GBZ/T 229):")
        for g in result["grades"]:
            print(f"  {g['factor']}: {g['name']} (G={g.get('g', '-')})")
    if result["ppe"]:
        print(f"\n🧤 PPE 建议 (GB 39800):")
        for p in result["ppe"]:
            items = ", ".join(f"{x['item']}({x['std']})" for x in p["ppe"])
            if items:
                print(f"  {p['factor']}: {items}")
    conn.close()


if __name__ == "__main__":
    main()
