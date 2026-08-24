"""复合报告解析器 — 从 申请报告PDF/可研/现状评价报告docx 中抽取结构化字段

真实场景: 企业提供的是成套报告文档(申请报告/可研/现状评价报告), 里面包含:
  项目概况(名称/投资/面积/性质/行业/产能/地点) / 工艺 / 设备 / 定员 / 防护 / 产品
系统需要"从复合文档里提取"这些字段, 而非只认独立表格文件。

用法:
  parse_report_file(path) -> dict   # 读 PDF/docx 全文, 抽字段
  (在 import_materials_from_dir 里, 遇 '申请报告|可研|现状评价|初步设计' 的 PDF/docx 时调用)
"""
import re, json


def _read_pdf(path):
    try:
        import pymupdf
        doc = pymupdf.open(str(path))
        return "\n".join(p.get_text() for p in doc)
    except Exception:
        return ""


def _read_docx(path):
    try:
        from docx import Document
        doc = Document(str(path))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        # 表格也读(设备/定员/原辅料表常在docx表格里)
        for tb in doc.tables:
            for row in tb.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts)
    except Exception:
        return ""


def read_full_text(path) -> str:
    """读 PDF/docx 全文(复合报告)"""
    suffix = str(path).lower()
    if suffix.endswith(".pdf"):
        return _read_pdf(path)
    if suffix.endswith((".docx", ".doc")):
        return _read_docx(path)
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _kv(text, key, maxlen=60):
    """'关键：值' 提取 (兼容 中文冒号/英文冒号)"""
    m = re.search(re.escape(key) + r"[\s]*[：:]\s*([^\n。；]{1,%d})" % maxlen, text)
    return m.group(1).strip() if m else ""


def _kv_alt(text, *keys):
    for k in keys:
        v = _kv(text, k)
        if v:
            return v
    return ""


def parse_report_file(path) -> dict:
    """从复合报告(申请报告/可研/现状评价)抽取字段"""
    text = read_full_text(path)
    if not text or len(text) < 200:
        return {}
    out = {}
    # 概况 — 匹配 "关键：值" 或 表格 "关键 | 值" (兼容 docx 表格/xlsx 单元格)
    def _field(*keys):
        for k in keys:
            # 1) "关键：值"
            v = _kv(text, k)
            if v:
                return v
            # 2) 表格/换行 "关键 值" (docx 表格单元格常被换行隔开)
            m = re.search(re.escape(k) + r"[\s：:|\u0007]{1,6}([^\n。；]{1,60})", text)
            if m:
                v = m.group(1).strip()
                # 排除纯数字单位/标点/过长
                if v and not re.fullmatch(r"[\d\s\.\-X,，、%吨米平方]+", v) and len(v) <= 60:
                    return v
        return ""
    out["name"] = _field("项目名称", "建设项目名称", "工程名称", "公司名称", "单位名称")
    out["industry"] = _field("所属行业", "行业类别", "行业分类", "国民经济行业", "行业")
    out["nature"] = _field("项目性质", "建设性质", "项目类别")
    out["location"] = _field("建设地点", "项目地点", "建设地址", "拟建地点", "项目地址")
    out["investment"] = _field("项目总投资", "总投资", "投资总额", "总投资概算", "投资")
    out["capacity"] = _field("生产规模", "设计规模", "建设规模", "产品方案", "生产能力", "年产量", "产能")
    out["area"] = _field("占地面积", "用地面积", "总用地面积", "建筑面积")
    out["risk"] = _field("职业病危害风险", "风险类别", "风险等级")

    # 工艺文本 (描述工艺段落)
    proc_lines = []
    for m in re.finditer(r"([^\n。]{20,400}(?:工艺|流程|工序)[^\n。]{10,300}[。\n])", text):
        seg = m.group(1).strip()
        if len(seg) > 40 and seg not in proc_lines:
            proc_lines.append(seg)
        if len(proc_lines) >= 3:
            break
    if proc_lines:
        out["process_text"] = "\n".join(proc_lines)

    # 设备 (全文扫设备特征词: 反应釜/稀释槽/储罐/泵/风机/塔/线等)
    eq = []
    # 优先"设备清单/主要设备"表段
    m = re.search(r"(?:主要设备|设备一览|主要生产设备)[^\n]{0,30}[:：]?\s*([^\n]{10,500})", text)
    seg = m.group(1) if m else text
    for w in re.findall(r"[\u4e00-\u9fff]{2,14}(?:反应釜|稀释槽|稀释釜|储罐|中间槽|调整槽|混合槽|冷却塔|水洗塔|洗涤塔|通风机|循环泵|水泵|风机|输送泵|配料釜|聚合釜|酯化釜|包装线|生产线)", seg):
        if w not in eq:
            eq.append(w)
    # 兜底: 全文扫 (设备清单段不足才补)
    if len(eq) < 5:
        for w in re.findall(r"([\u4e00-\u9fff]{2,10})(?:反应釜|稀释槽|稀释釜|储罐|槽|塔|泵|风机|包装线)", text):
            w2 = w.strip()
            if w2 and len(w2) >= 2 and w2 not in eq and not any(k in w2 for k in ("工艺", "流程", "生产", "车间", "仓库")):
                eq.append(w2)
    out["equipment"] = eq[:40]

    # 原辅材料 (从"原辅材料"表段落)
    mats = []
    for m in re.finditer(r"([^\n]{0,30}(?:苯乙烯|丙二醇|乙二醇|酸|醇|剂|树脂|甲醛|苯酐)[^\n]{0,30})", text):
        w = m.group(1).strip()
        if w and len(w) <= 40 and w not in mats:
            mats.append(w)
        if len(mats) >= 40:
            break
    out["materials"] = mats

    # 产品 (从"产品"表/产品方案)
    prods = []
    m = re.search(r"(?:产品方案|主要产品|产品情况)[^\n]{0,80}([\s\S]{0,600})", text)
    if m:
        for w in re.findall(r"[\u4e00-\u9fff]{3,16}(?:树脂|树脂\(|材料|溶液|剂|产品)", m.group(1)):
            if w not in prods:
                prods.append(w)
    out["products"] = prods[:20]

    # LLM 精化: 规则抽不准/抽不到的概况/设备/工序 用 LLM 语义抽取补充 (规则+LLM结合)
    # LLM只做语义抽取, 不碰危害判定(法规仍规则查表). 规则噪音大(投资抽成'万元'/设备碎片)时LLM更准
    try:
        if len(text) > 2000:  # 文本太长才值得调用LLM
            llm = llm_extract(text)
            # LLM 值优先(更准): name/investment/capacity/industry/area 等概况用LLM结果
            for f in ["name", "industry", "nature", "location", "investment", "capacity", "area"]:
                if llm.get(f) and str(llm[f]).strip() and str(llm[f]).strip() not in ("待补充", "未知"):
                    out[f] = str(llm[f]).strip()
            if llm.get("equipment") and isinstance(llm["equipment"], list):
                out["equipment"] = [str(e).strip() for e in llm["equipment"] if str(e).strip()][:40]
            if llm.get("materials") and isinstance(llm["materials"], list):
                out["materials"] = [str(m).strip() for m in llm["materials"] if str(m).strip()][:40]
            if llm.get("products") and isinstance(llm["products"], list):
                out["products"] = [str(p).strip() for p in llm["products"] if str(p).strip()][:20]
            if llm.get("process") and str(llm["process"]).strip() not in ("待补充",):
                out["process_text"] = str(llm["process"]).strip()
    except Exception:
        pass

    return out


def llm_extract(text: str) -> dict:
    """LLM语义抽取: 从复合报告文本抽结构化 概况/设备/工序/原辅料/产品 (规则噪音大时LLM补充).
    LLM只做语义抽取(工艺/报告文本→结构化字段), 不碰危害判定(法规部分仍规则查表)."""
    try:
        from web.llm_draft import _llm
        prompt = f"""从下面这份建设项目职业病危害预评价/现状评价相关报告文本中，抽取结构化字段，严格按JSON输出（不要多余文字）：

{{
  "name": "项目名称", "industry": "所属行业(含代码)", "nature": "项目性质(新建/改建/扩建/技改)",
  "location": "建设地点", "investment": "总投资(万元)", "capacity": "生产规模/产能",
  "area": "占地面积(㎡)", "equipment": ["主要设备名(反应釜/储罐/泵/风机/生产线等, 只列名称不含描述)"],
  "process": "生产工艺流程简述(一段话)", "materials": ["主要原辅料名(苯乙烯/丙二醇等, 只列名称)"],
  "products": ["产品名"], "staffing": "岗位定员(如'反应班X人/包装班X人')"
}}

要求：只输出JSON，字段没有的用空字符串/空数组；equipment/materials/products 只列名称，不要带"台/套/个"和描述；名称从报告实际内容提取。

报告文本（截选）：
{text[:6000]}"""
        raw = _llm(prompt)
        # 提取 JSON
        import re, json
        m = re.search(r"\{[\s\S]*\}", raw)
        d = json.loads(m.group(0)) if m else {}
        return d
    except Exception:
        return {}
