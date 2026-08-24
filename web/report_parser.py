"""复合报告解析器 — 从 申请报告/可研/现状评价报告 抽取结构化字段 (纯规则+表格, 不用LLM)

真实场景: 企业提供成套报告文档(申请报告/可研/现状评价报告), 内含概况/工艺/设备/定员/防护.
方案: PDF用pdfplumber(表格+正文键值), docx用python-docx.
  - 概况(名称/性质/地点/投资/规模/面积): 正文键值"关键:值" + 表格(序号|项目名称|单位|数量)
  - 设备: 设备表(含"设备名称"列) → 设备名 + 反应介质(危害线索)
  - 原辅料: 原辅表(含"物料名称"列) → 物料名 + 用量
  - 产品: 产品表(含"产品/名称"列) → 产品 + 产能
  - 工艺: 含"工艺/流程/工序"的关键段
原则: 数字(投资/产能/面积)用规则精确匹配, 不靠LLM(LLM幻觉风险); 字段缺失标空/待补充.
"""
import re


def read_full_text(path) -> str:
    """读 PDF/docx 全文(复合报告)"""
    suffix = str(path).lower()
    if suffix.endswith(".pdf"):
        try:
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                return "\n".join((p.extract_text() or "") for p in pdf.pages)
        except Exception:
            try:
                import pymupdf
                doc = pymupdf.open(str(path))
                return "\n".join(p.get_text() for p in doc)
            except Exception:
                return ""
    if suffix.endswith((".docx", ".doc")):
        try:
            from docx import Document
            doc = Document(str(path))
            parts = [p.text for p in doc.paragraphs if p.text.strip()]
            for tb in doc.tables:
                for row in tb.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
            return "\n".join(parts)
        except Exception:
            return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _tables_pdf(path):
    """提取PDF所有表格 (用于设备/原辅/产品/概况表)"""
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                for tb in page.extract_tables():
                    if tb:
                        yield tb
    except Exception:
        pass


def _tables_docx(path):
    """提取docx所有表格 (现状评价报告的设备/原辅/产品表在docx表格里)"""
    try:
        from docx import Document
        doc = Document(str(path))
        for tb in doc.tables:
            rows = []
            for row in tb.rows:
                rows.append([c.text.strip() for c in row.cells])
            if rows and any(any(c for c in r) for r in rows):
                yield rows
    except Exception:
        pass


def _all_tables(path):
    suf = str(path).lower()
    if suf.endswith(".pdf"):
        yield from _tables_pdf(path)
    elif suf.endswith((".docx", ".doc")):
        yield from _tables_docx(path)


def _kv(text, key, maxlen=60):
    """'关键：值' 提取 (兼容 中文/英文冒号, 排除单位词误配)"""
    m = re.search(re.escape(key) + r"[\s]*[：:]\s*([^\n。；]{1,%d})" % maxlen, text)
    if not m:
        return ""
    v = m.group(1).strip()
    # 排除纯单位词(万元/㎡/吨/个... 这些是值不是单位,但避免抓单位后面的)
    return v


def _number_after(text, *keys):
    """'关键...数字' 精确匹配 (投资5000万元/规模12000吨/面积10000㎡)"""
    for k in keys:
        m = re.search(re.escape(k) + r"[^\d]{0,15}(\d+\.?\d*)", text)
        if m:
            return m.group(1)
    return ""


def _table_field(tables, row_label, col_hint):
    """在表格里按行标签+列提示取值: 如 row_label='总投资' col_hint='万元'"""
    for tb in tables:
        header = [str(c).replace("\n", "").strip() for c in (tb[0] if tb else [])]
        # 找含 row_label 的行
        for row in tb:
            cells = [str(c).replace("\n", "").strip() for c in row]
            joined = " ".join(cells)
            if row_label in joined:
                # 该行里找数字(排除序号)
                for c in cells:
                    if re.fullmatch(r"\d+\.?\d*", c) and len(c) <= 12:
                        return c
    return ""


def parse_report_file(path) -> dict:
    """从复合报告抽取字段 (纯规则+表格, 不用LLM)"""
    text = read_full_text(path)
    if not text or len(text) < 200:
        return {}
    out = {}
    tables = list(_all_tables(path))

    # ===== 概况 (正文键值 + 表格数字) =====
    def _field(*keys):
        for k in keys:
            v = _kv(text, k)
            if v and v not in ("万元", "㎡", "吨", "个"):
                return v
        return ""
    out["name"] = _field("项目名称", "建设项目名称", "工程名称", "单位名称")
    out["industry"] = _field("所属行业", "行业类别", "行业分类", "国民经济行业")
    out["nature"] = _field("项目性质", "建设性质", "项目类别")
    out["location"] = _field("建设地点", "项目地点", "建设地址", "拟建地点", "项目地址")
    # 投资/产能/面积: 正文键值(带单位) + 表格数字
    inv = _kv(text, "项目总投资", 40) or _kv(text, "总投资", 40)
    if inv and "万元" in inv:
        out["investment"] = re.sub(r"[^\d.]", "", inv.split("万元")[0])
    if not out.get("investment"):
        n = _number_after(text, "项目总投资", "总投资", "工程总投资")
        if n:
            out["investment"] = n
    cap = _kv(text, "生产规模", 40) or _kv(text, "建设规模", 40) or _kv(text, "年产量", 40)
    if cap and len(cap) <= 40:
        out["capacity"] = cap
    if not out.get("capacity"):
        # 兜底: "新增X吨/年" / "X吨/年" / "年新增X吨" 数字 (长兴正文"年新增12000吨"/表格"12000 吨/年")
        m = re.search(r"(?:年新增|新增|设计产能|生产规模|产量)[^\d。；]{0,12}(\d+\.?\d*)\s*(?:吨|万)?(?:吨|t)", text)
        if m:
            out["capacity"] = m.group(1) + "吨/年"
        if not out.get("capacity"):
            m = re.search(r"(\d+\.?\d*)\s*吨/年", text)
            if m:
                out["capacity"] = m.group(1) + "吨/年"
    # 面积: 正文键值(带单位) + 表格(建设用地/占地面积 行的数字)
    out["area"] = _field("占地面积", "用地面积", "总用地面积", "建筑面积")
    if not out.get("area"):
        m = re.search(r"(?:占地面积|用地面积|建设用地)[^\d。；]{0,12}(\d+\.?\d*)\s*(?:㎡|m2|平方米|亩)", text)
        if m:
            out["area"] = m.group(1) + "㎡"

    # ===== 工艺文本 (含 工艺/流程/工序 的关键段) =====
    proc = []
    for m in re.finditer(r"([^\n。]{20,400}(?:工艺|流程|工序)[^\n。]{15,300}[。\n])", text):
        s = m.group(1).strip()
        if len(s) > 40 and s not in proc:
            proc.append(s)
        if len(proc) >= 3:
            break
    if proc:
        out["process_text"] = "\n".join(proc)

    # ===== 设备 (设备表: 含'设备名称'列 → 设备名 + 反应介质) =====
    eq, eq_detail = [], []
    for tb in tables:
        flat = " | ".join(str(c).replace("\n", "") for r in tb for c in r)
        if not ("设备名称" in flat or "设备" in flat and "名称" in flat):
            continue
        for row in tb:
            cells = [str(c).replace("\n", " ").strip() for c in row]
            if not cells:
                continue
            # 设备名称列 (找含"釜/槽/罐/泵/塔/机/炉/器/线"的单元格)
            eqname = ""
            for c in cells:
                if re.search(r"(反应釜|稀释槽|稀释釜|调整槽|中间槽|储罐|储槽|泵|冷凝器|冷凝|塔|风机|锅炉|搅拌|砂磨|分散|包装线|生产线|热媒)", c) and len(c) <= 20:
                    eqname = c
                    break
            if eqname and eqname not in eq_detail:
                eq_detail.append(eqname)
        if len(eq_detail) >= 40:
            break
    out["equipment"] = eq_detail[:40]

    # ===== 原辅材料 (原辅表: 含'物料名称'列) =====
    mats = []
    for tb in tables:
        flat = " | ".join(str(c).replace("\n", "") for r in tb for c in r)
        if not ("物料名称" in flat or "原辅" in flat or "原料名称" in flat):
            continue
        for row in tb:
            cells = [str(c).replace("\n", " ").strip() for c in row]
            for c in cells:
                c2 = c.split(" ")[0].split("（")[0].strip()
                if c2 and len(c2) <= 16 and re.search(r"(苯乙烯|乙二醇|丙二醇|二乙二醇|酸|醇|树脂|甲醛|苯酐|酐|酮|胺|酯|酚|剂|油)", c2) and c2 not in mats:
                    mats.append(c2)
        if len(mats) >= 40:
            break
    out["materials"] = mats[:40]

    # ===== 产品 (产品表: 含'产品'或'产量'列) =====
    prods = []
    _NOISE = ("产品", "中间产品", "新增不饱和树脂", "新增产品", "主要产品", "副产品", "产品方案")
    for tb in tables:
        flat = " | ".join(str(c).replace("\n", "") for r in tb for c in r)
        if "吨/年" not in flat and "年产量" not in flat and "产品方案" not in flat:
            continue
        for row in tb:
            cells = [str(c).replace("\n", " ").strip() for c in row]
            for c in cells:
                c2 = re.sub(r"[（(].*?[)）]", "", c).strip()
                c2 = re.sub(r"^\d+(\.\d+)?", "", c2).strip()  # 去前缀序号/数量
                c2 = c2.replace("吨/年", "").replace("产品", "").strip()
                if c2 and len(c2) <= 16 and (c2.endswith("树脂") or "树脂" in c2) \
                        and c2 not in prods and c2 not in _NOISE:
                    prods.append(c2)
        if len(prods) >= 20:
            break
    out["products"] = prods[:20]

    # ===== 额外字段: 定员/班制/检测/建构筑物/防护设施/PPE/体检 (现状报告表格) =====
    # 这些表格在 现状评价报告 docx 里 (定员表/检测表/建构筑物表/防护/PPE/体检)
    staffing, shifts = [], []
    buildings, facilities = [], []
    detections, health_check, ppe = [], [], []
    protection = out.get("protection", "")
    for tb in tables:
        if not tb:
            continue
        head0 = " ".join(str(c).replace("\n", "").strip() for c in tb[0])
        flat = " | ".join(str(c).replace("\n", " ") for r in tb for c in r)
        # 定员表 (部门|岗位|总人数|班制)
        if "岗位" in head0 and ("总人数" in flat or "人数" in flat):
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                if len(cells) >= 3 and cells[1] and cells[2].isdigit():
                    nh = {"post": cells[1], "count": cells[2], "dept": cells[0]}
                    if nh["post"] not in [s.get("post") for s in staffing]:
                        staffing.append(nh)
        # 班制 (工作班制列)
        if "工作班制" in flat:
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                if len(cells) >= 5 and any("班" in c for c in cells):
                    shifts.append({"system": cells[4] if len(cells) > 4 else "", "post": cells[1] if len(cells) > 1 else ""})
        # 检测表 (采样车间|采样点|粉尘/毒物|检测结果)
        if ("采样" in head0 or "检测结果" in flat) and "mg" in flat:
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                # 跳过表头/空行
                if not cells or any(c in ("采样点", "接触时间", "检测结果", "检测项目", "采样车间/岗位") for c in cells):
                    continue
                # 取 采样点/检测项目/结果 列(跳过表头词)
                vals = [c for c in cells if c and c not in ("采样点", "采样车间/岗位", "接触时间(h/d)", "接触时间")]
                if len(vals) >= 2:
                    detections.append({"factory": vals[0], "point": vals[0], "factor": vals[1],
                                       "result": vals[2] if len(vals) > 2 else ""})
        # 建构筑物表 (建筑物名称|占地|建面|层数)
        if "建构筑" in flat or ("建筑面积" in flat and "占地" in flat):
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                if len(cells) >= 2 and cells[0] and cells[0] != "建构筑物名称":
                    buildings.append({"name": cells[0], "area": cells[3] if len(cells) > 3 else "",
                                      "floor_area": cells[4] if len(cells) > 4 else ""})
        # 防护设施 (岗位|防护设施|数量)
        if "防护" in head0 or "设施" in flat and "数量" in flat:
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                if len(cells) >= 3 and cells[1]:
                    facilities.append({"post": cells[0], "facility": cells[1], "count": cells[2]})
        # PPE (岗位|防护用品|周期)
        if "防护用品" in flat or "个人防护" in flat:
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                if len(cells) >= 2 and cells[1]:
                    ppe.append({"item": cells[1], "post": cells[0]})
        # 体检表 (岗位|体检项目|周期)
        if "体检" in flat or "健康检查" in flat:
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                if len(cells) >= 2 and cells[1]:
                    health_check.append({"factor": cells[0], "category": cells[1]})
    if staffing:
        out["staffing"] = staffing[:50]
    if shifts:
        out["shifts"] = shifts[:30]
    if detections:
        out["detections"] = detections[:50]
    if buildings:
        out["buildings"] = buildings[:50]
    if facilities:
        out["facilities"] = facilities[:50]
    if ppe:
        out["ppe"] = ppe[:50]
    if health_check:
        out["health_check"] = health_check[:50]

    # ===== 公辅工程 (给水/排水/循环水/供配电/供热/压缩空气 段落) =====
    pw = []
    for m in re.finditer(r"([^\n。]{20,200}(?:给水|排水|循环冷却水|供配电|供电|供热|蒸汽|压缩空气|新鲜水|软水)[^\n。]{15,200}[。])", text):
        seg = m.group(1).strip()
        if len(seg) > 40 and seg not in pw:
            pw.append(seg)
        if len(pw) >= 6:
            break
    if pw:
        out["public_works"] = pw[:6]

    # ===== 应急/管理 (应急救援设施段 / 职业卫生管理措施段) =====
    emg = ""
    for m in re.finditer(r"([^\n。]{25,200}(?:应急喷淋|洗眼|急救药箱|应急救援设施|应急预案|呼吸器|救护)[^\n。]{15,250}[。])", text):
        seg = m.group(1).strip()
        if len(seg) > 50:
            emg = seg
            break
    if emg:
        out["emergency"] = emg
    mang = ""
    for m in re.finditer(r"([^\n。]{25,200}(?:职业卫生管理|管理制度|管理机构|管理组织|职业病防治责任)[^\n。]{15,250}[。])", text):
        seg = m.group(1).strip()
        if len(seg) > 50:
            mang = seg
            break
    if mang:
        out["management"] = mang

    return out
