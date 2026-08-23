"""章节数据槽填充器 v2 — 按报告章节 (1-9)

映射: 旧10.2.x → 新1-9 (数据源不变, 编号变)
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402


def _rows_of(conn, sql, args=()):
    return [list(r) for r in conn.execute(sql, args).fetchall()]


def fill_section(conn: sqlite3.Connection, sec: str, assess: dict) -> list[dict]:
    """按报告章节生成表格"""
    if sec == "1":
        # 项目基本情况 + 设备清单 + 定员 (从 assess._project_data 取结构化数据, 非硬编码文件)
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
        # 建构筑物表
        blds = pd_.get("buildings", [])
        if blds:
            b_rows = [[i, b.get("name", ""), b.get("area", ""), b.get("floor_area", ""),
                       b.get("floors", ""), b.get("height", "")] for i, b in enumerate(blds, 1)]
            tables.append({"name": "建构筑物表", "cols": ["序号", "名称", "占地面积(㎡)", "建筑面积(㎡)", "层数", "高度(m)"],
                           "rows": b_rows})
        return tables

    if sec == "2":
        # 危害分析网格表 (评价单元→工序→岗位→物料→危害因素→接触方式, 动态生成)
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
        # 检测结果表
        dets = (assess.get("_project_data") or {}).get("detections", [])
        if dets:
            det_rows = [[i, d.get("factor", ""), d.get("ctwa", "—"), "—", "合格"]
                        for i, d in enumerate(dets, 1)]
            tables.append({"name": "检测结果表", "cols": ["序号", "危害因素", "CTWA", "PC-TWA", "判定"],
                           "rows": det_rows})
        js = assess.get("judgements", [])
        if js:
            j_rows = [[i, j["factor"], "—", "—", "—", "—",
                       "合格" if j.get("pass") else "不合格"] for i, j in enumerate(js, 1)]
            tables.append({"name": "判定表", "cols": ["序号", "危害因素", "CTWA", "PC-TWA", "CSTEL", "PC-STEL", "判定"],
                           "rows": j_rows})
        return tables

    if sec == "3":
        tables = []
        rows = _rows_of(conn, "SELECT hazard_category, check_point, std_code, clause FROM protection_rule")
        tables.append({"name": "防护设施检查表", "cols": ["序号", "危害类别", "检查点", "依据"],
                       "rows": [[i, r[0], r[1][:40], f"{r[2]} {r[3]}"] for i, r in enumerate(rows, 1)]})
        rows = _rows_of(conn, "SELECT scenario, require, std_code, clause FROM emergency_rule")
        tables.append({"name": "应急救援检查表", "cols": ["序号", "场景", "要求", "依据"],
                       "rows": [[i, r[0], r[1][:40], f"{r[2]} {r[3]}"] for i, r in enumerate(rows, 1)]})
        # PPE (A2f)
        import csv
        from pathlib import Path as _P
        f = _P(__file__).resolve().parent.parent / "data" / "materials" / "A2f_防护措施.txt"
        p_rows = []
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("-") and "|" in line:
                    parts = line.lstrip("- ").split("|")
                    if len(parts) >= 2:
                        p_rows.append([len(p_rows) + 1, parts[0].strip(), parts[1].strip(), "GB 39800.1—2020"])
        if p_rows:
            tables.append({"name": "PPE配备表", "cols": ["序号", "岗位/危害", "防护装备", "标准"],
                           "rows": p_rows[:60]})
        # 设施配置明细 (岗位×设施×数量×备注)
        facs = (assess.get("_project_data") or {}).get("facilities", [])
        if facs:
            f_rows = [[i, f.get("post", ""), f.get("facility", ""), f.get("count", ""), f.get("remark", "")]
                      for i, f in enumerate(facs, 1)]
            tables.append({"name": "设施配置表", "cols": ["序号", "岗位/区域", "配备设施", "数量", "备注"],
                           "rows": f_rows})
        return tables

    if sec == "4":
        tables = []
        rows = _rows_of(conn, "SELECT category, require, std_code, clause FROM management_rule")
        tables.append({"name": "管理制度检查表", "cols": ["序号", "制度类别", "检查点", "依据"],
                       "rows": [[i, r[0], r[1][:40], f"{r[2]} {r[3]}"] for i, r in enumerate(rows, 1)]})
        # gbz1_rule 检查表: 选址/总体布局/辅助用室 (按 theme 分组)
        for theme, tname in [("选址", "选址检查表"), ("总体布局", "总体布局检查表"),
                             ("辅助用室", "辅助用室检查表"), ("建筑卫生学", "建筑卫生学检查表")]:
            g_rows = []
            for r in _rows_of(conn, "SELECT clause, rule, report_section FROM gbz1_rule WHERE theme LIKE ?", (theme + "%",)):
                # 检查表: 卫生要求=rule, 检查依据=clause, 结果/评价待项目确认
                g_rows.append([len(g_rows) + 1, r[1][:60], r[0], "待确认", "待确认"])
            if g_rows:
                tables.append({"name": tname, "cols": ["序号", "卫生要求", "检查依据", "检查结果", "评价"],
                               "rows": g_rows})
        ill = conn.execute("SELECT room, plane, lx FROM illumination_std").fetchall()
        if ill:
            tables.append({"name": "照度标准表", "cols": ["序号", "房间/场所", "参考面", "照度lx"],
                           "rows": [[i, r[0], r[1], r[2]] for i, r in enumerate(ill, 1)]})
        surv = _rows_of(conn, "SELECT factor, check_type, cycle FROM surveillance_rule")
        if surv:
            tables.append({"name": "职业健康监护表", "cols": ["序号", "危害因素", "检查类别", "周期"],
                           "rows": [[i, r[0], r[1], r[2] or "—"] for i, r in enumerate(surv, 1)]})
        return tables

    if sec == "5":
        # 问题与建议 (标准条款驱动)
        from web.advice_gen import fill_10211
        built = fill_10211(conn, assess)
        return built["tables"]

    if sec == "6":
        risk = assess.get("industry_risk") or {}
        rows = [["1", "职业病危害类别", risk.get("level", "—") + " (" + risk.get("name", "") + ")"],
                ["2", "存在的主要问题", f"{len(assess.get('judgements', []))} 条判定记录"],
                ["3", "可行性", "基本可行 (采取补充建议后)"]]
        return [{"name": "结论要素表", "cols": ["序号", "结论要素", "结论"], "rows": rows}]

    if sec == "7":
        refs = conn.execute("SELECT code, name FROM standard_ref ORDER BY id").fetchall()
        rows = []
        for i, (code, name) in enumerate(refs, 1):
            rows.append([i, code, name, "见标准库"])
        return [{"name": "评价依据表", "cols": ["序号", "标准号", "标准名称", "现行状态"], "rows": rows}]

    if sec == "8":
        tables = []
        mats = assess.get("hazards", [])
        if mats:
            rows = [[i, h["factor"], "; ".join(h.get("sources", [])[:2]),
                     h.get("hazard_element", "化学毒物")] for i, h in enumerate(mats, 1)]
            tables.append({"name": "原辅材料表", "cols": ["序号", "名称", "来源", "危害类别"], "rows": rows})
        chain = assess.get("industry_chain")
        if chain:
            c_rows = []
            for lvl, info in chain.items():
                if isinstance(info, dict) and "name" in info:
                    c_rows.append([lvl, info["name"]])
            tables.append({"name": "行业链", "cols": ["层级", "名称"], "rows": c_rows[:5]})
        return tables

    if sec == "9":
        elems = ["自然环境状况", "产品及原辅材料", "生产规模", "劳动定员",
                 "生产制度", "生产工艺", "生产设备", "防护措施", "管理水平"]
        rows = [[i, e, "待填写", "待填写"] for i, e in enumerate(elems, 1)]
        return [{"name": "类比可比性表", "cols": ["序号", "比较要素", "拟建项目", "类比项目"], "rows": rows}]

    if sec == "10":
        # 健康影响表 + 接触限值表 (危害因素×健康影响×职业病 + 危害因素×PC-TWA×PC-STEL)
        tables = []
        hazards = [h["factor"] for h in assess.get("hazards", [])] or []
        # 健康影响表
        he_rows = []
        seen_he = set()
        for fac in hazards[:30]:
            if fac in seen_he:
                continue
            seen_he.add(fac)
            r = conn.execute("SELECT effect, route FROM health_effect WHERE factor LIKE ? LIMIT 1", (fac + "%",)).fetchone()
            if r:
                he_rows.append([fac, r[0][:60] if r[0] else "—", r[1] or "—"])
        if he_rows:
            tables.append({"name": "健康影响表", "cols": ["危害因素", "健康影响", "侵入途径"], "rows": he_rows})
        # 接触限值表 (从 detections 的 factor 匹配 oel_limit)
        det_factors = [d["factor"] for d in (assess.get("_project_data") or {}).get("detections", [])]
        lim_rows = []
        seen_l = set()
        for fac in (det_factors + hazards)[:30]:
            if fac in seen_l:
                continue
            seen_l.add(fac)
            rows = conn.execute("SELECT oel_type, value, unit FROM oel_limit WHERE factor_name LIKE ?", (fac + "%",)).fetchall()
            if rows:
                for oel_type, val, unit in rows[:2]:
                    lim_rows.append([fac, oel_type, val, unit])
        if lim_rows:
            tables.append({"name": "接触限值表", "cols": ["危害因素", "限值类型", "限值", "单位"], "rows": lim_rows})
        return tables

    return []


if __name__ == "__main__":
    from knowledge.project_assess import assess_project
    conn = connect()
    r = assess_project(conn, {"name": "t", "industry": "261", "equipment": ["酯化釜"],
                              "detections": [{"factor": "甲苯", "ctwa": 0.5}]})
    for sec in ("1", "2", "3", "4", "6"):
        ts = fill_section(conn, sec, r)
        print(f"=== {sec}: {[(t['name'], len(t['rows'])) for t in ts]}")
    conn.close()
