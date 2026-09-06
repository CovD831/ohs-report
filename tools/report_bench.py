#!/usr/bin/env python3
"""
报告 BenchMark — 通用报告评测器 (确定性层)
================================================
对任意生成的预评价报告 docx + 项目 data 做确定性指标评测:
  A. 数据一致性: 报告内插值(投资/产能/定员/面积)数字交叉检查
  B. 数据溯源: 报告中的关键数字/危害因素, 追溯项目 data 来源, 抓幻觉
  C. 限值正确性: 报告引用的职业接触限值 vs oel_limit 标准库
  D. 结构完整性: 章节树 vs 标准结构(原报告/SUBS)
  E. 表格完整性: 应有表 vs 实际表
  F. 全量性: materials/equipment/detections 条数 vs 项目 data

规则版本: 修改规则必须 bump BENCH_VERSION, 并往 bench_calib.py 加校准案例
(改进闭环: 发现评测误判 → 加案例 → 改规则 --regress 回归 → bump 版本)

用法:
  python tools/report_bench.py --docx path/to/report.docx [--pid <项目id>] [--db data/ohs.db]
  python tools/report_bench.py --docx path/to/report.docx --reference path/to/原报告.docx

输出: JSON (确定性指标结果) — 供上层 LLM-judge 层汇总
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 版本号: 每次修改评测规则(增加/收紧/放松判定)必须 bump
BENCH_VERSION = "1.4.0"


# ============ A. 数据一致性: 报告内数字交叉检查 ============
def check_internal_consistency(text: str) -> list[dict]:
    """同一语义字段在报告多处出现的数值必须一致 (如 产能12000 不能一处5000一处12000)"""
    issues = []
    # 语义字段 → 数值提取模式 (通用: 不绑定行业)
    field_patterns = {
        "产能": r"(?:本项目产能|新增产能|扩建后产能|本项目生产规模|年生产规模|设计产能|本项目年产|本次新增)\D{0,6}(\d{2,6})(?:\s*(?:吨|t|万|t/a|吨/年))",
        "投资": r"(?:投资总额|项目总投资|总投资)\D{0,4}(\d{2,6})(?:\s*(?:万元|亿|元))",
        # 定员: 只匹配"全厂/总定员"口径 (接触人数/危害因素人数是另一口径, 不混比)
        "定员": r"(?:全厂定员|全厂职工|全厂员工|总定员|员工总数|劳动定员)\D{0,6}(\d{2,5})(?:\s*(?:人|名))",
        "占地面积": r"(?:本项目占地|全厂占地|总占地面积)\D{0,6}(\d{3,7})(?:\s*(?:㎡|m2|亩|m²))",
    }
    # 去数字中的千分位/空格
    def norm_num(s: str) -> str:
        return re.sub(r"[\s,，]", "", s)
    for field, pat in field_patterns.items():
        vals = re.findall(pat, text)
        vals = [norm_num(v) for v in vals if v]
        uniq = set(vals)
        # 产能字段: 扩建项目允许 新增值 + 全厂值 并存; 只当存在多个"新增/本项目"值才报
        if field == "产能":
            # 本项目/新增 语境的数值
            proj_vals = re.findall(r"(?:本项目|本次新增|扩建后新增|新增)\D{0,8}(\d{2,6})(?:吨|t/a|吨/年)", text)
            proj_uniq = set(norm_num(v) for v in proj_vals if v)
            if len(proj_uniq) > 1:
                issues.append({
                    "type": "internal_inconsistency", "rule_id": "A-01",
                    "field": field,
                    "values_found": sorted(proj_uniq),
                    "severity": "high",
                    "note": f"本项目/新增产能值不一致: {sorted(proj_uniq)} (全厂总量可并存)",
                })
            continue
        if len(uniq) > 1:
            issues.append({
                "type": "internal_inconsistency", "rule_id": "A-01",
                "field": field,
                "values_found": sorted(uniq),
                "severity": "high",
                "note": f"同一字段出现不一致数值: {sorted(uniq)}",
            })
    return issues


# ============ B. 数据溯源: 报告数字/危害因素 追溯项目 data ============
def check_traceability(text: str, project_data: dict) -> list[dict]:
    """报告中的关键实体(危害因素/产品/原料), 必须是项目 data 里有的 (抓幻觉)"""
    issues = []
    data_factors = set()
    for h in (project_data.get("hazards") or []):
        if isinstance(h, dict):
            data_factors.add(str(h.get("factor") or ""))
        else:
            data_factors.add(str(h))
    # grid 里的 factors 也纳入
    for g in (project_data.get("hazard_grid") or []):
        if isinstance(g, dict):
            for f in str(g.get("factors") or "").split("、"):
                if f.strip():
                    data_factors.add(f.strip())
    for d in (project_data.get("detections") or []):
        data_factors.add(str(d.get("factor") or ""))
    for m in (project_data.get("materials") or []):
        if isinstance(m, dict):
            data_factors.add(str(m.get("name") or ""))
    # 报告正文里出现的"关键危害因素" 模式 (限值上下文等) — 精确词/短语 (避免"苯"命中"甲苯")
    pattern = re.compile(
        r"(苯乙烯|甲苯|二甲苯|乙二醇|丙二醇|二乙二醇|甲醇|乙醇|异丙醇|硫酸|盐酸|氢氧化钠|氟化物|"
        r"氟化氢|氯化氢|氢氟酸|氨|甲醛|丙烯酰胺|苯酚|正己烷|矽尘|滑石粉尘|电焊烟尘|白炭黑|"
        r"聚乙烯粉尘|其他粉尘|噪声|高温|局部振动|工频电场|邻苯二甲酸酐|马来酸酐|甲基丙烯酸甲酯)")
    norm_data = {f.replace(" ", "").replace("（", "(").replace("）", ")") for f in data_factors if f}
    # 豁免: 限值表/健康影响表/标准条文里的因子 (如甲醛/氨/乙醇 — GBZ条文引用, 非报告编造)
    # (校准案例 C006: 限值表引用因子 ≠ 溯源失败)
    import sqlite3 as _s3
    _con = None
    std_factors = set()
    try:
        from knowledge.oel import connect as _ocon
        _con = _ocon()
        std_factors = {str(r[0]).strip() for r in _con.execute(
            "SELECT DISTINCT name FROM oel_limit WHERE name LIKE '%' ").fetchall()}
        std_factors |= {str(r[0]).strip() for r in _con.execute(
            "SELECT DISTINCT hazard_name FROM hazard_factor LIMIT 2000").fetchall()}
    except Exception:
        pass
    if not std_factors:
        # 回退: 标准条文专有因子 (限值表/健康影响表引用, 非项目检测因子 — 检测因子必须溯源)
        std_factors = {"甲醛", "氨", "乙醇", "甲醇", "甲苯", "二甲苯", "苯乙烯", "盐酸", "硫酸",
                       "氢氧化钠", "氟化氢", "氯化氢", "氢氟酸", "苯酚", "正己烷", "丙酮",
                       "乙酸", "乙酯", "二氯甲烷", "丙烯酰胺", "苯胺", "硫化氢", "一氧化碳",
                       "二氧化硫", "三氧化铬", "铬酸盐", "镍", "锰", "铅", "汞", "焦炉逸散物",
                       "高温", "噪声", "振动", "局部振动", "工频电场", "紫外辐射", "微波辐射",
                       "激光辐射", "X射线", "γ射线"}
        # 说明: 物理因素(高温/噪声/振动/电场等)是GBZ 2.2标准条文引用, 豁免溯源;
        #      检测类因子(聚乙烯粉尘/白炭黑/电焊烟尘/滑石粉尘/其他粉尘)不放回退集 —
        #      这些出现在报告正文且项目data无对应 → 应报幻觉(C004)
    seen = set()
    for m in pattern.findall(text):
        if m in seen:
            continue
        seen.add(m)
        # 精确匹配: data 中是否有相同短语 (去括号/空白) — 不用子串(避免"苯"匹配"甲苯")
        nm = m.replace(" ", "").replace("（", "(").replace("）", ")")
        if nm in std_factors or any(nm in f or f in nm for f in std_factors if f):
            continue  # 标准库因子(限值表/危害因子表) → 豁免
        if not any(nm == d for d in norm_data):
            issues.append({
                "type": "untraceable_factor", "rule_id": "B-01",
                "factor": m,
                "severity": "medium",
                "note": f"报告中出现危害因素「{m}」但项目数据中无对应因子 (可能是幻觉或遗漏)",
            })
    return issues


def basis_set(items: list) -> list:
    return list(dict.fromkeys(items))


# ============ C. 限值正确性: 报告引用的限值 vs oel_limit 库 ============
def check_oel_citations(text: str, conn) -> list[dict]:
    def _normalize(s: str) -> str:
        return re.sub(r"[\s,，]", "", s.lower())
    issues = []
    # 报告中的限值引用形如 "PC-TWA 5 mg/m³" / "PC-TWA(总尘) 5mg/m³"
    cites = re.findall(r"PC-TWA[^0-9]{0,12}(\d+(?:\.\d+)?)\s*(?:mg/m3|mg/m³|mg/m3)", text, re.I)
    for c in cites:
        # 查库: 有 PC-TWA 限值 == 报告值?
        rows = conn.execute("SELECT DISTINCT value FROM oel_limit WHERE type LIKE '%TWA%'").fetchall()
        std_vals = {_normalize(str(r[0])) for r in rows}
        cv = _normalize(c)
        if cv not in std_vals:
            issues.append({
                "type": "oel_mismatch", "rule_id": "C-01",
                "reported": c,
                "severity": "high" if cv else "low",
                "note": f"报告引用限值 PC-TWA {c} mg/m³ 未在标准库 oel_limit 中找到精确值"
            })
    return issues


# ============ D. 结构完整性: 章节树 vs 标准 ============
def check_structure(path: Path, standard_sections: list) -> list[dict]:
    import docx, re
    d = docx.Document(str(path))
    titles = set()
    for p in d.paragraphs:
        t = re.sub(r"\s+", " ", p.text.strip())
        m = re.match(r"^(\d+(?:\.\d+)*)\s+(.+)", t)
        if m:
            titles.add(m.group(1))
    issues = []
    for key in standard_sections:
        if key not in titles:
            issues.append({
                "type": "missing_section", "rule_id": "D-01",
                "section": key,
                "severity": "high",
                "note": f"缺少标准章节 {key}"
            })
    return issues


# ============ E. 表格完整性 ============
def check_tables(path: Path, project_data: dict) -> list[dict]:
    import docx
    d = docx.Document(str(path))
    n = len(d.tables)
    issues = []
    # 关键表: 原辅材料/设备/检测/定员/防护/PPE/应急
    required = []
    if project_data.get("materials"): required.append("原辅材料")
    if project_data.get("equipment"): required.append("设备")
    if project_data.get("detections"): required.append("检测")
    if project_data.get("staffing"): required.append("定员")
    if project_data.get("protection"): required.append("防护设施")
    if project_data.get("ppe"): required.append("PPE")
    if project_data.get("emergency"): required.append("应急")
    # 表头关键词 (宽匹配: 真实报告的表格表头命名有多种)
    kw_map = {
        "原辅材料": ("原辅", "原料", "物料"),
        "设备": ("设备",),
        "检测": ("检测", "CTWA", "采样", "粉尘种类", "职业接触"),
        "定员": ("定员", "岗位", "工种", "人数", "班组"),
        "防护设施": ("防护", "隔声", "通风", "除尘", "防毒"),
        "PPE": ("防护用品", "个人防护", "防护装备"),
        "应急": ("应急", "救援", "应急物资", "配备设施", "放置点位"),
    }
    tbl_heads = []
    # 表前标题段落匹配: "表7.1 应急救援物资清单" 这类标题 (表头无"应急"但标题有)
    from docx.table import Table as _T
    from docx.text.paragraph import Paragraph as _P
    from docx.oxml.ns import qn as _qn
    prev_titles = []
    cur_title = ""
    for el in d.element.body:
        if el.tag == _qn("w:p"):
            pp = _P(el, d)
            t = pp.text.strip()
            if t.startswith("表") and len(t) < 60:
                cur_title = t
            else:
                cur_title = ""
        elif el.tag == _qn("w:tbl"):
            prev_titles.append(cur_title or "")
    _ti = 0
    for tb in d.tables:
        if tb.rows:
            h = " ".join(c.text.strip()[:12] for c in tb.rows[0].cells)
            if len(tb.rows) > 1:
                h2 = " ".join(c.text.strip()[:12] for c in tb.rows[1].cells)
                h = h + " " + h2
            # 表头 + 表前标题 (联合匹配)
            if _ti < len(prev_titles) and prev_titles[_ti]:
                h = h + " " + prev_titles[_ti]
            _ti += 1
            tbl_heads.append(h)
    for req in required:
        kws = kw_map[req]
        if not any(any(k in h for k in kws) for h in tbl_heads):
            issues.append({
                "type": "missing_table", "rule_id": "E-01",
                "table": req,
                "severity": "medium",
                "note": f"缺少主题表: {req} (表内未找到含 {kws} 的表头)"
            })
    # E-02 表格种类齐全度: 物理因素限值表 (噪声/高温/振动 — GBZ 2.2 静态表, 任何项目接触物理因素就需要)
    # (盲区修复: 之前只查"有数据驱动的表", GBZ 2.2 标准限值表这种'通用必备表'没查)
    phys_required = []
    _allfactors = " ".join(str(h.get("factor") or "") for h in (project_data.get("hazards") or [])) + \
                  " " + " ".join(str(g.get("factors") or "") for g in (project_data.get("hazard_grid") or []))
    if "噪声" in _allfactors:
        phys_required.append(("噪声限值", ("噪声", "声级", "等效声级")))
    if "高温" in _allfactors:
        phys_required.append(("高温限值", ("高温", "WBGT")))
    if "振动" in _allfactors:
        phys_required.append(("振动限值", ("振动",)))
    for req, kws in phys_required:
        if not any(any(k in h for k in kws) for h in tbl_heads):
            issues.append({
                "type": "missing_table", "rule_id": "E-02",
                "table": req,
                "severity": "medium",
                "note": f"缺少物理因素限值表: {req} (项目危害含{req[:2]}, 应有GBZ 2.2对应限值表)"
            })
    return issues


# ============ F. 全量性: 条数 vs 项目 data ============
def check_completeness(path: Path, project_data: dict) -> list[dict]:
    import docx, re
    d = docx.Document(str(path))
    issues = []
    # 原料表行数 vs 项目 materials 条数
    proj_mats = len(project_data.get("materials") or [])
    proj_eq = len(project_data.get("equipment") or [])
    proj_det = len(project_data.get("detections") or [])
    # docx 统计
    n_mat_rows = 0
    n_eq_rows = 0
    n_det_rows = 0
    for tb in d.tables:
        head = " ".join(c.text.strip()[:10] for c in tb.rows[0].cells)
        if "原辅" in head and "材料" in head:
            n_mat_rows = max(n_mat_rows, len(tb.rows) - 1)
        if "设备" in head and ("名称" in head or "明细" in head):
            n_eq_rows = max(n_eq_rows, len(tb.rows) - 1)
        if "检测" in head and ("CTWA" in head or "结果" in head):
            n_det_rows = max(n_det_rows, len(tb.rows) - 1)
        # 检测表头变体: 无"检测"字样但含 CTWA/危害因素 (如"序号 危害因素 CTWA PC-TWA 判定")
        if "检测结果" not in head and "CTWA" in head and "危害因素" in head:
            n_det_rows = max(n_det_rows, len(tb.rows) - 1)
    if proj_mats > 0 and n_mat_rows < proj_mats * 0.8:
        issues.append({"type": "truncated", "rule_id": "F-01", "data": "materials",
                       "expected": proj_mats, "actual": n_mat_rows,
                       "severity": "high", "note": f"原料表 {n_mat_rows} 行 < 项目数据 {proj_mats} 条(80%截断线)"})
    if proj_eq > 0 and n_eq_rows < proj_eq * 0.8:
        issues.append({"type": "truncated", "rule_id": "F-01", "data": "equipment",
                       "expected": proj_eq, "actual": n_eq_rows,
                       "severity": "medium", "note": f"设备表 {n_eq_rows} 行 < 项目数据 {proj_eq} 条"})
    if proj_det > 0 and n_det_rows < proj_det * 0.8:
        issues.append({"type": "truncated", "rule_id": "F-01", "data": "detections",
                       "expected": proj_det, "actual": n_det_rows,
                       "severity": "high", "note": f"检测表 {n_det_rows} 行 < 项目数据 {proj_det} 条"})
    return issues


# ============ 主入口 ============
def main():
    ap = argparse.ArgumentParser(description="报告确定性评测")
    ap.add_argument("--docx", required=True, help="报告 docx 路径")
    ap.add_argument("--pid", help="项目 id (查 data/ohs.db)")
    ap.add_argument("--db", default="data/ohs.db", help="sqlite 数据库路径")
    ap.add_argument("--reference", help="参考报告 docx (原报告, 用于结构对比)")
    ap.add_argument("--project-json", help="项目 data JSON (替代 pid)")
    args = ap.parse_args()

    # 读项目 data
    project_data = {}
    if args.project_json:
        project_data = json.loads(Path(args.project_json).read_text())
    elif args.pid:
        import sqlite3
        conn = sqlite3.connect(args.db)
        row = conn.execute("SELECT data FROM project WHERE id=?", (args.pid,)).fetchone()
        if row:
            project_data = json.loads(row[0])
        conn.close()

    # 评测输入自检: project-json 若截断(如 materials 前80)会误报溯源失败 — 显式警告
    _mats = project_data.get("materials") or []
    if args.project_json and _mats and len(_mats) < 100 and not project_data.get("_truncated_ok"):
        print(f"⚠️ 警告: --project-json 仅含 {len(_mats)} 条 materials — 若为截断快照, B维度(溯源)"
              f"可能误报'因子无对应'。建议 --pid 或传全量 data JSON。", file=sys.stderr)

    text = ""
    import docx
    d = docx.Document(args.docx)
    text = "\n".join(p.text for p in d.paragraphs)

    # 标准结构: 取 SUBS/SUBS3 (若报告生成器提供)
    std_sections = []
    try:
        from web.report_struct import CHAPTERS, SUBS, SUBS3
        for ch in CHAPTERS:
            std_sections.append(ch)
            for sn, _ in SUBS.get(ch, []):
                std_sections.append(sn)
                for sub3, _ in SUBS3.items():
                    if sub3.split(".")[0] == ch:
                        std_sections.append(sub3)
    except Exception:
        pass

    results = {"docx": args.docx, "pid": args.pid, "bench_version": BENCH_VERSION}
    results["A_internal_consistency"] = check_internal_consistency(text)
    results["B_traceability"] = check_traceability(text, project_data)
    results["D_structure"] = check_structure(Path(args.docx), std_sections)
    results["E_tables"] = check_tables(Path(args.docx), project_data)
    results["F_completeness"] = check_completeness(Path(args.docx), project_data)

    # 汇总分数: 每维度 0-100
    def _score(items):
        if not items:
            return 100
        # 高严重度 25 分/条, 中 12 分, 低 6 分
        penalty = sum((25 if i.get("severity") == "high" else 12 if i.get("severity") == "medium" else 6) for i in items)
        return max(0, 100 - penalty)
    results["scores"] = {
        "data_consistency": _score(results["A_internal_consistency"]),
        "traceability": _score(results["B_traceability"]),
        "structure": _score(results["D_structure"]),
        "tables": _score(results["E_tables"]),
        "completeness": _score(results["F_completeness"]),
    }
    results["total_deterministic"] = round(sum(results["scores"].values()) / 5, 1)

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
