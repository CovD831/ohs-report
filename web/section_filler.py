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
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402


def _rows_of(conn, sql, args=()):
    return [list(r) for r in conn.execute(sql, args).fetchall()]


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
            p_rows = [[i, p.get("name", ""), p.get("output", "") or pd_.get("capacity", "—")]
                      for i, p in enumerate(prods, 1)]
            tables.append({"name": "产品产量表", "cols": ["序号", "产品名称", "年产量"], "rows": p_rows})
        blds = pd_.get("buildings", [])
        if blds:
            b_rows = [[i, b.get("name", ""), b.get("功能区", ""), b.get("火灾危险类别", ""),
                       b.get("耐火等级", ""), b.get("floors", ""), b.get("area", ""),
                       b.get("floor_area", ""), b.get("height", "")] for i, b in enumerate(blds, 1)]
            tables.append({"name": "建构筑物表", "cols": ["序号", "名称", "功能区", "火灾危险类别", "耐火等级", "层数", "占地面积(㎡)", "建筑面积(㎡)", "高度(m)"],
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
            b_rows = [[i, b.get("name", ""), b.get("功能区", ""), b.get("火灾危险类别", ""),
                       b.get("耐火等级", ""), b.get("floors", ""), b.get("area", ""),
                       b.get("floor_area", ""), b.get("height", "")] for i, b in enumerate(blds, 1)]
            tables.append({"name": "建构筑物表", "cols": ["序号", "名称", "功能区", "火灾危险类别", "耐火等级", "层数", "占地面积(㎡)", "建筑面积(㎡)", "高度(m)"],
                           "rows": b_rows})
        # 原辅材料表 (materials dict 完整列: name/规格/年用量/最大储量/物态/储存地点)
        mats2 = pd_.get("materials", [])
        if mats2:
            m_rows = []
            for i, m in enumerate(mats2, 1):
                if isinstance(m, dict):
                    m_rows.append([i, m.get("name", ""), m.get("规格", ""), m.get("年用量", ""),
                                   m.get("最大储量", ""), m.get("物态", ""), m.get("储存地点", "")])
                else:
                    m_rows.append([i, str(m), "—", "—", "—", "—", "—"])
            tables.append({"name": "原辅材料表", "cols": ["序号", "原辅材料名称", "规格", "年用量(t/a)", "最大储量(t)", "物态", "储存地点"],
                           "rows": m_rows})
        # 产品产量表 (含变化量)
        prods = pd_.get("products", [])
        if prods:
            p_rows = [[i, p.get("name", "") if isinstance(p, dict) else p,
                       p.get("output", "") or pd_.get("capacity", "—"),
                       p.get("变化量", "") if isinstance(p, dict) else ""]
                      for i, p in enumerate(prods, 1)]
            tables.append({"name": "产品产量表", "cols": ["序号", "产品名称", "年产量", "变化量"], "rows": p_rows})
        # 项目概况/投资 (从固定字段)
        invest = (pd_.get("investment") or "")
        cap = (pd_.get("capacity") or "")
        if invest or cap or pd_.get("area"):
            info_rows = [
                ["投资总额", invest or "—"],
                ["建设规模/产能", cap or "—"],
                ["占地面积", pd_.get("area", "—")],
                ["项目性质", pd_.get("nature", "—")],
            ]
            tables.append({"name": "项目概况表", "cols": ["项目", "内容"], "rows": info_rows})
        # 设备细表 (名称+规格+数量)
        eq_d = pd_.get("equipment_detail", [])
        if eq_d:
            ed_rows = [[i, d.get("name", ""), d.get("spec", ""), d.get("qty", "")]
                       for i, d in enumerate(eq_d, 1)]
            tables.append({"name": "设备明细表", "cols": ["序号", "设备名称", "规格型号", "数量"], "rows": ed_rows})
        # 班制定员表 (工种×一班..合计)
        sf = pd_.get("shifts", [])
        if sf:
            s_rows = [[i, s.get("system", ""), s.get("b1", ""), s.get("b2", ""),
                       s.get("b3", ""), s.get("b4", ""), s.get("total", "")]
                      for i, s in enumerate(sf, 1)]
            tables.append({"name": "班制定员表", "cols": ["序号", "工种/系统", "一班", "二班", "三班", "四班", "合计"],
                           "rows": s_rows})
        # 主要职业病危害因素表 (危害→来源→类别; 改名避免与'原辅材料表'冲突, 该表用 hazards)
        mats = assess.get("hazards", [])
        if mats:
            rows = [[i, h["factor"], "; ".join(h.get("sources", [])[:2]),
                     h.get("hazard_element", "化学毒物")] for i, h in enumerate(mats, 1)]
            tables.append({"name": "主要职业病危害因素表", "cols": ["序号", "名称", "来源", "危害类别"], "rows": rows})
        # 检查表: 选址/总体布局/建筑卫生学/辅助用室 (依据GBZ1标准库)
        for theme, tname in [("选址", "选址检查表"), ("总体布局", "总体布局检查表"),
                             ("建筑卫生学", "建筑卫生学检查表"), ("辅助用室", "辅助用室检查表")]:
            g_rows = []
            for r in _rows_of(conn, "SELECT clause, rule, report_section FROM gbz1_rule WHERE theme LIKE ?", (theme + "%",)):
                g_rows.append([len(g_rows) + 1, r[1][:60], r[0], "待确认", "待确认"])
            if g_rows:
                tables.append({"name": tname, "cols": ["序号", "卫生要求", "检查依据", "检查结果", "评价"],
                               "rows": g_rows})
        ill = conn.execute("SELECT room, plane, lx FROM illumination_std").fetchall()
        if ill:
            tables.append({"name": "照度标准表", "cols": ["序号", "房间/场所", "参考面", "照度lx"],
                           "rows": [[i, r[0], r[1], r[2]] for i, r in enumerate(ill, 1)]})
        # 辅助用室设置表 (名称×位置×要求, 从 gbz1 辅助用室 theme)
        aux_rows = []
        for r in _rows_of(conn, "SELECT clause, rule FROM gbz1_rule WHERE theme LIKE '辅助用室'"):
            if r[0] and r[0].startswith(("7.3", "7.2", "7.4")):
                aux_rows.append([len(aux_rows) + 1, r[1][:40], "详见GBZ 1", r[0]])
        if aux_rows:
            tables.append({"name": "辅助用室设置表", "cols": ["序号", "辅助用室/要求", "设置位置", "依据"], "rows": aux_rows[:15]})
        # 噪声分级表 (噪声暴露等级)
        nz_rows = []
        for r in _rows_of(conn, "SELECT value, note FROM oel_limit WHERE factor_name LIKE '%噪声%' LIMIT 3"):
            nz_rows.append(r)
        if nz_rows:
            tables.append({"name": "噪声分级表", "cols": ["噪声限值", "说明"], "rows": nz_rows})
        return tables

    if sec == "4":
        # 类比调查: 类比可比性表 + 检测结果 + 职业健康监护
        tables = []
        elems = ["自然环境状况", "产品及原辅材料", "生产规模", "劳动定员",
                 "生产制度", "生产工艺", "生产设备", "防护措施", "管理水平"]
        rows = [[i, e, "待填写", "待填写"] for i, e in enumerate(elems, 1)]
        tables.append({"name": "类比可比性表", "cols": ["序号", "比较要素", "拟建项目", "类比项目"], "rows": rows})
        # 检测结果表 (按类型分张: 毒物/粉尘/噪声/高温, 有检测数据才出)
        dets = (assess.get("_project_data") or {}).get("detections", [])
        if dets:
            from collections import defaultdict
            det_by_type = defaultdict(list)
            for d in dets:
                fac = d.get("factor", "")
                cat = conn.execute("SELECT category FROM hazard_factor WHERE name LIKE ? LIMIT 1", (fac + "%",)).fetchone()
                ty = (cat[0] if cat else "化学毒物")
                if "噪声" in fac:
                    ty = "噪声"
                elif "高温" in fac or "WBGT" in fac:
                    ty = "高温"
                det_by_type[ty].append(d)
            for ty, dets_b in det_by_type.items():
                dtype = {"粉尘": "粉尘", "噪声": "噪声", "高温": "高温"}.get(ty, "化学毒物")
                det_rows = [[i, d.get("factor", ""), d.get("ctwa", "—"), "—", "合格"]
                            for i, d in enumerate(dets_b, 1)]
                tables.append({"name": f"检测结果表({dtype})", "cols": ["序号", "危害因素", "CTWA", "PC-TWA", "判定"],
                               "rows": det_rows})
        surv = _rows_of(conn, "SELECT factor, check_type, cycle FROM surveillance_rule")
        if surv:
            tables.append({"name": "职业健康监护表", "cols": ["序号", "危害因素", "检查类别", "周期"],
                           "rows": [[i, r[0], r[1], r[2] or "—"] for i, r in enumerate(surv, 1)]})
        return tables

    if sec == "5":
        # 危害因素及危害程度分析: 识别/判定/检测/限值/健康影响/关键控制点
        tables = []
        from web.hazard_grid import build_grid
        grid = build_grid(conn, assess.get("_project_data", {}))
        if grid:
            g_rows = []
            for g in grid:
                g_rows.append([g["unit"], g["process"], "、".join(g["posts"][:3]),
                               g["enclosed"] or "—", "、".join(g["materials"][:3]),
                               "、".join(g["factors"][:6])])
            tables.append({"name": "危害因素识别表", "cols": ["评价单元/车间", "工序", "岗位/工种",
                           "设备密闭", "物料/中间产物", "主要危害因素"], "rows": g_rows})
        # 检测结果表 (按类型分张)
        dets = (assess.get("_project_data") or {}).get("detections", [])
        if dets:
            from collections import defaultdict
            det_by_type = defaultdict(list)
            for d in dets:
                fac = d.get("factor", "")
                cat = conn.execute("SELECT category FROM hazard_factor WHERE name LIKE ? LIMIT 1", (fac + "%",)).fetchone()
                ty = (cat[0] if cat else "化学毒物")
                if "噪声" in fac:
                    ty = "噪声"
                elif "高温" in fac or "WBGT" in fac:
                    ty = "高温"
                det_by_type[ty].append(d)
            for ty, dets_b in det_by_type.items():
                dtype = {"粉尘": "粉尘", "噪声": "噪声", "高温": "高温"}.get(ty, "化学毒物")
                det_rows = [[i, d.get("factor", ""), d.get("ctwa", "—"), "—", "合格"]
                            for i, d in enumerate(dets_b, 1)]
                tables.append({"name": f"检测结果表({dtype})", "cols": ["序号", "危害因素", "CTWA", "PC-TWA", "判定"],
                               "rows": det_rows})
        js = assess.get("judgements", [])
        if js:
            j_rows = [[i, j["factor"], "—", "—", "—", "—",
                       "合格" if j.get("pass") else "不合格"] for i, j in enumerate(js, 1)]
            tables.append({"name": "判定表", "cols": ["序号", "危害因素", "CTWA", "PC-TWA", "CSTEL", "PC-STEL", "判定"],
                           "rows": j_rows})
        # 接触限值表 (危害因素×PC-TWA×PC-STEL×PE×单位, 从 oel_limit)
        det_factors = [d["factor"] for d in dets] + [h["factor"] for h in assess.get("hazards", [])]
        lim_rows, seen_l = [], set()
        for fac in det_factors:
            if not fac or fac in seen_l:
                continue
            seen_l.add(fac)
            for r in conn.execute("SELECT oel_type, value, unit FROM oel_limit WHERE factor_name LIKE ?", (fac + "%",)).fetchall()[:3]:
                lim_rows.append([fac, r[0], r[1], r[2]])
        if lim_rows:
            tables.append({"name": "接触限值表", "cols": ["危害因素", "限值类型", "限值", "单位"], "rows": lim_rows})
        # 工种×危害表 (岗位→危害, 从网格关联)
        if grid:
            gw_rows = []
            for g in grid:
                for p in (g.get("posts") or ["各岗位"])[:2]:
                    gw_rows.append([g["unit"], p, "、".join(g["factors"][:6])])
            if gw_rows:
                tables.append({"name": "工种危害表", "cols": ["评价单元", "岗位/工种", "主要危害因素"], "rows": gw_rows})
        # 健康影响表 (危害因素×健康影响×职业病×侵入途径, 用 entity_link 链)
        hazards = [h["factor"] for h in assess.get("hazards", [])] or []
        he_rows = []
        seen_he = set()
        from web.entity_link import link_health_effect, link_disease, is_physical
        for fac in hazards[:30]:
            if fac in seen_he:
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
            tables.append({"name": "健康影响表", "cols": ["危害因素", "健康影响", "所致职业病", "侵入途径"], "rows": he_rows})
        # 关键控制点表 (岗位×关键因子×措施)
        kc_rows = []
        for i, j in enumerate(js[:10]):
            lv = (j.get("level") or {}).get("level", "—")
            ctrl = (j.get("level") or {}).get("control", "")
            kc_rows.append([i + 1, j["factor"], lv, ctrl[:40]])
        if kc_rows:
            tables.append({"name": "关键控制点表", "cols": ["序号", "关键控制因子", "控制级别", "控制措施"], "rows": kc_rows})
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
        tables.append({"name": "防护设施检查表", "cols": ["序号", "危害类别", "检查点", "依据"],
                       "rows": [[i, r[0], r[1][:40], f"{r[2]} {r[3]}"] for i, r in enumerate(rows, 1)]})
        # 设施配置明细 (岗位×设施×数量×备注)
        facs = (assess.get("_project_data") or {}).get("facilities", [])
        if facs:
            f_rows = [[i, f.get("post", ""), f.get("facility", ""), f.get("count", ""), f.get("remark", "")]
                      for i, f in enumerate(facs, 1)]
            tables.append({"name": "设施配置表", "cols": ["序号", "岗位/区域", "配备设施", "数量", "备注"],
                           "rows": f_rows})
        return tables

    if sec == "7":
        # 应急救援: 应急救援检查表
        rows = _rows_of(conn, "SELECT scenario, require, std_code, clause FROM emergency_rule")
        return [{"name": "应急救援检查表", "cols": ["序号", "场景", "要求", "依据"],
                 "rows": [[i, r[0], r[1][:40], f"{r[2]} {r[3]}"] for i, r in enumerate(rows, 1)]}]

    if sec == "8":
        # 个人防护用品: PPE配备表
        import csv
        f = Path(__file__).resolve().parent.parent / "data" / "materials" / "A2f_防护措施.txt"
        p_rows = []
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("-") and "|" in line:
                    parts = line.lstrip("- ").split("|")
                    if len(parts) >= 2:
                        p_rows.append([len(p_rows) + 1, parts[0].strip(), parts[1].strip(), "GB 39800.1—2020"])
        if p_rows:
            return [{"name": "PPE配备表", "cols": ["序号", "岗位/危害", "防护装备", "标准"], "rows": p_rows[:60]}]
        return []

    if sec == "9":
        # 职业卫生管理: 管理制度检查表 + 职业健康监护表
        tables = []
        rows = _rows_of(conn, "SELECT category, require, std_code, clause FROM management_rule")
        tables.append({"name": "管理制度检查表", "cols": ["序号", "制度类别", "检查点", "依据"],
                       "rows": [[i, r[0], r[1][:40], f"{r[2]} {r[3]}"] for i, r in enumerate(rows, 1)]})
        surv = _rows_of(conn, "SELECT factor, check_type, cycle FROM surveillance_rule")
        if surv:
            tables.append({"name": "职业健康监护表", "cols": ["序号", "危害因素", "检查类别", "周期"],
                           "rows": [[i, r[0], r[1], r[2] or "—"] for i, r in enumerate(surv, 1)]})
        return tables

    if sec == "10":
        # 补充建议: 问题与建议 (标准条款驱动)
        from web.advice_gen import fill_10211
        built = fill_10211(conn, assess)
        return built["tables"]

    if sec == "11":
        # 结论: 结论要素表
        risk = assess.get("industry_risk") or {}
        rows = [["1", "职业病危害类别", risk.get("level", "—") + " (" + risk.get("name", "") + ")"],
                ["2", "存在的主要问题", f"{len(assess.get('judgements', []))} 条判定记录"],
                ["3", "可行性", "基本可行 (采取补充建议后)"]]
        return [{"name": "结论要素表", "cols": ["序号", "结论要素", "结论"], "rows": rows}]

    return []


if __name__ == "__main__":
    from knowledge.project_assess import assess_project
    conn = connect()
    r = assess_project(conn, {"name": "t", "industry": "261", "equipment": ["酯化釜"],
                              "detections": [{"factor": "甲苯", "ctwa": 0.5}]})
    for sec in ("1", "3", "5", "6", "11"):
        ts = fill_section(conn, sec, r)
        print(f"=== {sec}: {[(t['name'], len(t['rows'])) for t in ts]}")
    conn.close()
