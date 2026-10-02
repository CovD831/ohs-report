"""数字溯源断言 — 报告里每一个数字必须能指回原始材料

背景 (用户红线):
  用户不信任 LLM 从文档/报告提取的数字 (会合成/拼凑, 如长兴 investment=5000 是 LLM 拼的)。
  坚持: 数字用 pdfplumber 表格提取 + 规则正则可确定取; LLM 仅兜底
  "设备/工序/物料名称" 类语义字段, 绝不给数字。

本模块的作用: 不阻止流程, 而是把"这个数字哪来的"变成可审计的事实。
  - 每个数字要么带 evidence (能指回材料文件某处), 要么显式标 unverified。
  - 导出前跑一遍, 机器判断哪些数字无据。无据的单独列出来, 交给质检面板暴露。

三种来源 (由强到弱):
  rule   — 规则/正则从材料提取, 附带 evidence 出处。最可信。
  std    — 标准库查表命中 (oel_limit / gbz1_rule / ppe_item 等)。可信, 可复算。
  llm    — LLM 生成。默认可疑: 必须带 evidence, 否则判 unverified。

evidence 形状 (由调用方给, 本模块不强求格式, 只做归一化):
  {"file": "材料.pdf", "page": 3, "text": "原文片段…"} 或
  {"file": "材料.docx", "text": "…"} 或纯 str (视为原文片段, 无文件名)
"""
import os
import re

# ============ 数字识别 ============
# 核心难点: 报告里"数字"和"名称里的数字"混在一起 —
#   真实数据: 2054.4(t/a)、12(人)、6.5
#   名称/型号: 2-甲基-13-丙二醇、CT6.5、SEC-115、AYFBJ-40-200
# 判据 (按优先级):
#   1. 带单位 (mg/m³ / 吨 / 万元 …) → 一定是数据
#   2. 紧跟中文量词/单位字 → 一定是数据
#   3. 前面或后面紧贴 ASCII 字母 → 型号/编号, 排除 (CT6.5 / SEC-115)
#   4. 后面紧跟 '-' + 数字 (如 "2-甲基") → 化学名位次, 排除
#   5. 纯序号列 (1,2,3…) → 由 audit 按列名识别 (序号/编号), 不是数据

# 数字 token 基础形态: 支持 1,234.5 / 12.5% / ≤0.5 / ≥3 / 1.2×10^3
# 前导比较符 (≤≥<>) 不入 token, 便于与后续字符判断
_NUM_RE = re.compile(
    r"\d[\d,]*(?:\.\d+)?(?:\s*[×xX*]\s*10\s*\^?\s*-?\d+)?"
)

# 明显不是"数据"的噪声: 标准号
_NOISE_RES = [
    re.compile(r"^GBZ?\s*\d"),            # 标准号 GBZ 2.1
    re.compile(r"^GB\s*\d"),
]

# 紧跟"年"的4位数字 (年份) — 除非带单位
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

UNIT_RE = re.compile(
    r"(?:mg/m³|mg/m3|μg/m³|ug/m3|mg/kg|dB\(A\)|dB|℃|°C|%|‰|"
    r"吨/年|t/a|kg/a|m³/h|m3/h|平方米|㎡|m²|万元|"
    r"毫米|厘米|千米|公里|平米)"
)
# 中文量词/单位跟在数字后 (⚠ 不含"年/月/日" — 那是日期不是量)
# ⚠ 必须含 ℃/°C/℉: 早缺此项 → "反应釜高温100℃" 被判成"中文紧贴数字"误删真实数据
CN_UNIT_RE = re.compile(
    r"^\s*(?:℃|°C|℉|%|‰|人|台|套|个|次|天|小时|分钟|秒|米|吨|千克|公斤|克|"
    r"立方米|平方米|米/秒|度|分贝|个/班|人/班|班|层|间|座|辆|项|条|种|处|点|万|亿|千瓦|千瓦时|升|毫升|伏|安|赫兹|帕|兆帕|千帕)"
)
# 紧贴 ASCII 字母 (型号/编号)
_LETTER_ADJ = re.compile(r"[A-Za-z]")
# 化学名位次: 数字 + '-' + 中文 (2-甲基 / 1,2-丙二醇)
_CHEM_POS = re.compile(r"^\d[\d,]*\s*[-—]\s*[\u4e00-\u9fff]")
# 设备位号尾号: 中文名 + 数字 (加料槽1 / 反应釜2 / 1号车间)
# ⚠ 实测把 "加料槽1" 的 1 当数据 → 误报 unverified (危害因素识别表/关键控制点表)
_EQUIP_TAG = re.compile(r"[\u4e00-\u9fff]\s*\d+\s*(?:$|[、，,；;）)】]|过程中)")
# 时间/频次表述: 5d/w、4h/d、每班约操作4小时、8h 等效声级
# (这些是**工况描述**不是检测结果; 值本身来自标准或班制, 不属"待溯源数字")
_TIME_EXPR = re.compile(
    r"(?:\d+\s*[dD]\s*/\s*[wW])|"          # 5d/w
    r"(?:\d+\s*[hH]\s*/\s*[dD])|"          # 8h/d
    r"(?:每班|每天|每周|每年|工作时|操作)\s*约?\s*\d+\s*(?:小时|分钟|天|周)|"
    r"(?:\d+\s*h\s*等效)")
# 标称比例/时间率 (表头属性, 非测量值): 100% / 75% 作为接触时间率
_NOMINAL_RATIO = re.compile(r"^(?:100|75|50|25)\s*%$")


def cell_numbers(text) -> list[str]:
    """取出一个单元格/句子里所有"数据性"数字 token (滤掉型号/化学名位次/序号)"""
    if text is None:
        return []
    s = str(text)
    out = []
    for m in _NUM_RE.finditer(s):
        tok = m.group(0).strip()
        if not re.search(r"\d", tok):
            continue
        if any(r.search(tok) for r in _NOISE_RES):
            continue
        start, end = m.start(), m.end()
        # 向前跳过空白看真实前邻字符
        p = start - 1
        while p >= 0 and s[p] in " \u3000":
            p -= 1
        prev_ch = s[p] if p >= 0 else ""
        # 3. 紧邻 ASCII 字母 → 型号/编号 (CT6.5 / SEC-115 / AYFBJ-40-200)
        if _LETTER_ADJ.match(prev_ch or ""):
            continue
        # 3b. 前邻是 '-' 且再往前紧邻 ASCII 字母 → "SEC-115" 的 115
        if prev_ch in "-—" and p > 0:
            q = p - 1
            while q >= 0 and s[q] in " \u3000":
                q -= 1
            if q >= 0 and _LETTER_ADJ.match(s[q] or ""):
                continue
        # 化学名位次: 前邻是 '-' 且再往前也是数字 → 1,2-丙二醇 的第二段
        if prev_ch in "-—" and p > 0:
            q = p - 1
            while q >= 0 and s[q] in " \u3000":
                q -= 1
            if q >= 0 and s[q].isdigit():
                continue
        # tok 自身含字母 → 型号
        if _LETTER_ADJ.search(tok.rstrip("%")):
            continue
        # 4. 化学名位次 2-甲基 / 13-丙二醇 (后面跟 '-' + 中文)
        if _CHEM_POS.match(tok):
            continue
        # 后视: tok 后紧跟 '-' + 中文 → 化学名位次
        if re.match(r"^\s*[-—]\s*[\u4e00-\u9fff]", s[end:end + 8]):
            continue
        # 后视: tok 后紧跟 ASCII 字母 → 型号片段 (40-200 里的 40 后是 '-')
        if re.match(r"^\s*[-—]\s*[A-Za-z0-9]", s[end:end + 8]) and not UNIT_RE.search(
                s[max(0, end - len(tok)):end + 8]):
            continue
        # 2. 带单位 或 紧跟中文量词 → 一定是数据
        has_unit = bool(UNIT_RE.search(tok)) or bool(CN_UNIT_RE.match(s[end:end + 6]))
        if has_unit:
            # 但"标称比例/时间率"除外 (100% 作接触时间率是表头属性, 非测量值)
            if _NOMINAL_RATIO.match(tok):
                continue
            out.append(tok)
            continue
        # 5. 设备位号: 数字**紧贴**中文名 (中间无空格) 且无单位 → 编号不是数据 (加料槽1/反应釜2)
        #    ⚠ 判据是"**紧贴**": 用原始 s[start-1] 判, **不能**用跳过空格后的 prev_ch —
        #       否则 "温度 25℃" 的 25 也会被误判为位号 (前字符是 '度')。
        #       `加料槽1` 紧贴; `温度 25℃` / `年耗量 2054.4 t/a` 中间有空格 → 是数据。
        #    ⚠ 且必须在 has_unit 之后判: "反应釜高温100℃" 带单位 → 是数据。
        _raw_prev = s[start - 1] if start > 0 else ""
        if _raw_prev and re.match(r"[\u4e00-\u9fff]", _raw_prev) \
                and not re.match(r"^\s*[.]\d", s[end:end + 6]):
            continue
        # 5b. 时间/频次表述 (5d/w、每班约操作4小时) → 工况描述, 非检测结果
        if _TIME_EXPR.search(s[max(0, start - 8):end + 8]):
            continue
        # 年份: 4位 (19xx/20xx) 紧跟"年" 且无单位 → 不是材料数据 (2024年)
        if _YEAR_RE.match(tok) and re.match(r"^\s*年", s[end:end + 3]):
            continue
        # 1. 纯数字 (2054.4 / 12 / 3) — 保留, 交由列名/上下文判定
        out.append(tok)
    return out


# 序号列/编号列: 该列数字属于结构编号, 不是材料数据
_INDEX_COL_RE = re.compile(r"^(序号|编号|序|no\.?|#|位号)$", re.I)


def _is_index_col(name: str) -> bool:
    return bool(_INDEX_COL_RE.match(str(name or "").strip()))


def _norm_evidence(ev) -> dict:
    """ev 归一化成 {file, page, text}"""
    if not ev:
        return {}
    if isinstance(ev, str):
        return {"file": "", "page": None, "text": ev}
    if isinstance(ev, dict):
        return {"file": str(ev.get("file") or ev.get("source") or ""),
                "page": ev.get("page"),
                "text": str(ev.get("text") or ev.get("snippet") or "")}
    return {}


def _evidence_ok(ev) -> bool:
    """有据 = 至少给出原文片段 或 可定位的文件名之一; 空 dict 视为无据

    ⚠ 早期要求「file 且 page 非空」→ **docx/xlsx 提取的数据被误判无据**
      (Word 表格没有"页码"概念, 只有文件名+表格定位), 实测使检测结果表大面积误报 unverified。
    现放宽: 有 text(原文片段) 或 有 file(**具体的源文件名**) 即算有据;
      "未命名/检测报告" 这类占位不算 (那是没记来源)。
    """
    if not ev:
        return False
    ev = _norm_evidence(ev)
    # ① 有原文片段 → 有据
    if str(ev.get("text") or "").strip():
        return True
    # ② 无 text 时: 需要**具体**来源文件名 (占位名不算)
    f = str(ev.get("file") or "").strip()
    placeholder = ("", "检测报告", "未知", "—")
    if f not in placeholder:
        return True
    # ③ 标准库类: file 是"标准库"+ table 指明具体表名 → 有据
    if f == "标准库" and str(ev.get("table") or "").strip():
        return True
    return False


def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return " ".join(str(x) for x in v)
    return str(v)


def audit_tables(built_tables: dict) -> dict:
    """扫描 built_tables, 逐表逐格统计数字与来源标记

    built_tables[表名] = {cols, rows, ..., "prov": {...}}  (prov 可选)
    prov 两种粒度:
      表级: "prov": {"source": "rule", "evidence": {...}}
      格级: "prov": {"<行号>_<列号>": {"source": "llm", "evidence": {...}}, "_default": {...}}

    返回 {summary, tables:[{name, nums, with_ev, unverified, samples:[...]}]}
    """
    report = []
    total_nums = with_ev = 0
    unver_total = 0
    review_total = 0

    for tname, t in (built_tables or {}).items():
        if not isinstance(t, dict):
            continue
        prov = t.get("prov") or {}
        default = prov.get("_default") if isinstance(prov, dict) else None
        # 表级 prov (无 _default/格级时作为整表默认)
        if not default and isinstance(prov, dict) and (prov.get("source") or prov.get("evidence")):
            default = prov
        rows = t.get("rows") or []
        # 列名 (双层表取第二层优先, 更具体): 用于识别序号/编号列
        col1 = [str(c) for c in (t.get("cols") or [])]
        col2 = [str(c) for c in (t.get("header2") or [])]
        colnames = [(col2[i] if i < len(col2) and col2[i] else (col1[i] if i < len(col1) else ""))
                    for i in range(max(len(col1), len(col2)))]

        t_nums = t_ev = t_review = 0
        samples = []
        for ri, row in enumerate(rows):
            cells = list(row) if isinstance(row, (list, tuple)) else [row]
            for ci, cell in enumerate(cells):
                # 序号/编号列: 结构编号而非材料数据 → 不计入 (如 "序号" 列的 1,2,3)
                if ci < len(colnames) and _is_index_col(colnames[ci]):
                    continue
                for tok in cell_numbers(_cell_text(cell)):
                    t_nums += 1
                    source = None
                    evidence = None
                    if isinstance(prov, dict):
                        cp = prov.get(f"{ri}_{ci}")
                        if cp:
                            source = cp.get("source")
                            evidence = _norm_evidence(cp.get("evidence"))
                    if source is None and default:
                        source = default.get("source")
                        evidence = _norm_evidence(default.get("evidence"))
                    source = (source or "unknown").lower()
                    # 有据判定:
                    #   ⚠ untraced/unknown **显式判无据** (优先于 evidence 判据) —
                    #      prov_rule(traceable=False) 的降级语义必须在审计侧也成立,
                    #      否则放宽 _evidence_ok 后 "untraced + 有file" 会被误判有据 (实测踩过)。
                    #   rule/std: 可信来源 (prov_rule 已保证"带出处才叫 rule")
                    #   其余(llm/vision/...): 需自带 evidence
                    if source in ("untraced", "unknown"):
                        ok = False
                    else:
                        ok = _evidence_ok(evidence) or source in ("rule", "std")
                    # vision (扫描件视觉识别): 带页码 → 算"有据但需人工核对",
                    # 单列在 needs_review 里, 不混入 rule (用户红线: 机器识别≠确定提取)
                    if ok and source == "vision":
                        t_review += 1
                    if ok:
                        t_ev += 1
                    else:
                        samples.append({
                            "table": tname, "row": ri, "col": ci, "value": tok,
                            "source": source,
                            "cell": _cell_text(cell)[:60],
                        })
        total_nums += t_nums
        with_ev += t_ev
        review_total += t_review
        unver = t_nums - t_ev
        unver_total += unver
        report.append({
            "name": tname, "nums": t_nums, "with_evidence": t_ev,
            "needs_review": t_review,
            "unverified": unver,
            "unverified_samples": samples[:8],
        })

    report.sort(key=lambda x: (-x["unverified"], x["name"]))
    return {
        "summary": {
            "tables": len(report),
            "numbers": total_nums,
            "with_evidence": with_ev,
            # vision 来源 (扫描件视觉识别): 有据但建议人工核对, 单列不混入 rule
            "needs_review": review_total,
            "unverified": unver_total,
            "ok": unver_total == 0,
        },
        "tables": report,
    }


def audit_standards(data: dict) -> dict:
    """标准引用审计: 扫 section_states 正文, 找出**废止/无效**标准引用

    事故 (2026-10): 正文引 GBZ 2.2—2007 / GBZ 188—2014 等已废止标准 (61 处)。
    与数字审计同级的**引用审计** —— 数字要有据, 标准引用也必须现行有效。

    输出 {total_refs, superseded:[{sec,ref,current}], unknown:[{sec,ref,suggest}]}
    """
    import re
    states = (data or {}).get("section_states") or {}
    try:
        from web.validators import _check_standards
    except Exception:
        return {"total_refs": 0, "superseded": [], "unknown": []}
    seen: set[tuple] = set()
    superseded, unknown = [], []
    n_refs = 0
    re_any = re.compile(r"(?<![0-9A-Za-z])GB(?:/T|/Z)?\s*\d+(?:\.\d+)?(?:\s*[—\-–]\s*(?:19|20)\d{2})?"
                        r"(?![0-9A-Za-z])"
                        r"|(?<![0-9A-Za-z])GBZ(?:/T)?\s*\d+(?:\.\d+)?(?:\s*[—\-–]\s*(?:19|20)\d{2})?"
                        r"(?![0-9A-Za-z])")
    for sec, v in states.items():
        text = (v or {}).get("text", "") if isinstance(v, dict) else ""
        if not text:
            continue
        for m in re_any.finditer(text):
            n_refs += 1
        for it in _check_standards(text):
            key = (sec, it.get("standard"), it.get("level"))
            if key in seen:
                continue
            seen.add(key)
            rec = {"sec": sec, "ref": it.get("standard"), "note": it.get("note", "")}
            if it.get("level") == "error":
                rec["current"] = it.get("current") or ""
                superseded.append(rec)
            else:
                rec["suggest"] = it.get("suggest") or ""
                unknown.append(rec)
    return {"total_refs": n_refs, "superseded": superseded, "unknown": unknown}


def audit_project(data: dict) -> dict:
    """按 project.data 跑一遍审计 (供 API/面板调用)"""
    bt = (data or {}).get("built_tables") or {}
    res = audit_tables(bt)
    if isinstance(res, dict):
        try:
            res["standards"] = audit_standards(data)
        except Exception:
            pass
    return res


# ============ 供建表侧使用的标记助手 ============

def prov_rule(evidence=None, field: str = "", traceable: bool = True) -> dict:
    """规则/正则抽取所得。

    ⚠ 关键诚实性约束: 只有该值能在材料里指到出处时才叫 rule。
    上游提取器若本身不可信 (例如 investment 字段可能是 LLM 从报告里"拼"出来的),
    必须传 traceable=False → 降级为 untraced, 审计时判 unverified。
    宁可说"我证不了", 也不能给可疑数字盖上"非 LLM 生成"的章。
    """
    ev = _norm_evidence(evidence)
    if traceable and _evidence_ok(ev):
        d: dict = {"source": "rule", "evidence": ev}
    else:
        # 规则路径但无出处 → 标记为 untraced (审计会判 unverified, 逼人补出处)
        d = {"source": "untraced"}
        if field:
            d["field"] = field
        if ev:
            d["evidence"] = ev
    if field:
        d["field"] = field
    return d


def prov_std(ref: str = "") -> dict:
    """标准库查表所得 (oel_limit / gbz1_rule / ppe_item …), ref=库表名+主键"""
    d: dict = {"source": "std"}
    if ref:
        d["evidence"] = {"file": f"标准库:{ref}", "page": None, "text": f"标准库:{ref}"}
    return d


def prov_llm(evidence=None, field: str = "") -> dict:
    """LLM 生成所得。⚠ 无 evidence 的数字会被审计判为 unverified。
    field 应只用于语义字段 (设备/工序/物料名称); 数字字段请勿用本函数。"""
    d: dict = {"source": "llm"}
    if field:
        d["field"] = field
    ev = _norm_evidence(evidence)
    if ev:
        d["evidence"] = ev
    return d


def prov_vision(evidence=None, field: str = "", page=None) -> dict:
    """**视觉模型**从扫描件识别所得 (2026-09 新增)。

    为什么单独一类, 不并进 rule:
      扫描件无文本层, 数值是模型"看图读出来的", 存在识别误差
      (实测 tesseract 会把 5mg/m³ 读成 Smg/ma; 视觉模型好得多但仍非确定提取)。
      必须与 "pdfplumber/正则确定取出" 区分, 否则溯源面板会把
      "机器猜的数" 伪装成 "确定提取的数" —— 这正是用户红线要防的。

    审计策略: vision 带 evidence (文件名+页码) → 记为"有据但需人工核对",
    在面板里单独显示为 vision 来源, 不混入 rule。
    """
    d: dict = {"source": "vision", "needs_review": True}
    if field:
        d["field"] = field
    ev = _norm_evidence(evidence)
    if ev and page is not None:
        ev["page"] = page
    if ev:
        d["evidence"] = ev
    return d


def flag_llm_numbers(built_tables: dict) -> list[dict]:
    """只挑出 LLM 来源的数字 (用户最关心的那类), 供面板突出显示"""
    rep = audit_tables(built_tables)
    out = []
    for t in rep["tables"]:
        for s in t["unverified_samples"]:
            if s["source"] == "llm":
                out.append(s)
    return out


if __name__ == "__main__":  # 本地自测
    import json as _json
    import sys as _sys
    demo = {
        "原辅材料表": {
            "cols": ["序号", "名称", "年耗量(t/a)"],
            "rows": [[1, "乙醇", "12.5"], [2, "丙酮", "8"]],
            "prov": {"_default": prov_rule({"file": "材料.pdf", "page": 2, "text": "乙醇 12.5 t/a"})},
        },
        "项目概况表": {
            "cols": ["序号", "项目名称", "指标"],
            "rows": [[3, "项目投资总额", "5000"]],
            "prov": {"_default": prov_llm()},   # 无 evidence → 应判 unverified
        },
        "气象因素表": {
            "cols": ["指标", "值"],
            "rows": [["年平均气温℃", "15.8"], ["地质", "待补充"]],
        },
    }
    print(_json.dumps(audit_tables(demo), ensure_ascii=False, indent=2))
