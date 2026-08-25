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
    if not out.get("industry"):
        # 从叙述提取行业代码+名: "属于'26 化学原料和化学制品制造业'...'265 合成材料制造''C2651 初级形态塑料及合成树脂制造'"
        m = re.search(r"([A-Z]?\d{3,4})\s*([\u4e00-\u9fff]{2,12})(?:制造|业)", text)
        if m:
            out["industry"] = m.group(1) + m.group(2) + "制造"
        else:
            # 兜底: "XX 制造业" + 代码
            m = re.search(r"([A-Z]?\d{3})\s*([\u4e00-\u9fff]{2,12})", text)
            if m:
                out["industry"] = m.group(1) + m.group(2)
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
        # 优先"新增X吨"(本项目新增产能) — "年新增 X 吨" 紧跟, 数字马上出现
        m = re.search(r"(?:年新增|新增|设计产能|本项目建成后|扩建新增)\s*(\d+\.?\d*)\s*(?:万吨|吨)?(?:\s*t)?", text)
        if m:
            out["capacity"] = m.group(1) + "吨/年"
        if not out.get("capacity"):
            # 兜底: "新增 X 吨/年"(可跨少量字)
            m = re.search(r"(?:年新增|新增|设计产能)\D{0,6}(\d+\.?\d*)\s*吨/年", text)
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
                # 去空格/数字/逗号/吨 残留(如"不 饱和聚酯树脂"/"乙烯 基树脂、5000")
                c2 = re.sub(r"[\s、,，/]", "", c2)
                c2 = re.sub(r"\d+(\.\d+)?", "", c2)
                c2 = c2.strip()
                # 过滤: 含"工艺/生产/工序/方案"的不是产品名(如"新增不饱和树脂生产工艺")
                if re.search(r"(工艺|生产工序|生产方案|方案|生产线)", c2) or len(c2) > 16:
                    continue
                if c2 and (c2.endswith("树脂") or "树脂" in c2) \
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
    hazard_grid_list = []  # 危害识别网格 (评价单元|岗位|产品|工段|危害因素)
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
        # 检测表 (采样车间|采样点|粉尘/毒物|检测结果) — 需跳过"采样时间"说明行+表头重复行
        if "采样" in head0 and ("mg" in flat or "CTWA" in flat or "检测结果" in flat):
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                if not cells or not any(cells):
                    continue
                # 跳过"采样时间"跨列说明行
                if any("采样时间" in c for c in cells):
                    continue
                # 跳过表头重复行(首列表头词 / 含CTWA/判定等表头)
                if cells[0] in ("采样车间/岗位", "采样点", "接触时间", "检测结果", "检测项目") \
                        or any(c in ("CTWA", "CSTEL", "CME", "判定结果", "PE/PC-TWA", "接触时间(h/d)") for c in cells):
                    continue
                # 跳过纯数字/短表头行
                if len(cells[0]) < 2 or re.fullmatch(r"\d+(\.\d+)?", cells[0]):
                    continue
                # factor: 危害因子词启发式(非数字单元格)
                factor = ""
                for c in cells:
                    if c and len(c) <= 22 and re.search(r"(苯乙烯|苯|粉尘|滑石|甲苯|二甲苯|乙二醇|丙二醇|甲醇|丙醇|丁酮|丙酮|乙酸|乙酯|氢氧化|矽|游离|锰|臭氧|氮氧化物|硫酸|镍|铬|锑|炭黑|白炭黑|电焊烟尘|矽尘|树脂|铅|汞|噪声|高温|振动)", c) and not re.match(r"^\d", c):
                        factor = c
                        break
                if not factor:
                    continue
                # result: 检测结果/CTWA 数字(取第一个数值单元格, 兼容 <0.33 这种检出限值)
                ctwa = ""
                for c in cells[2:]:
                    if re.fullmatch(r"([<>≤≥]?\s*)?\d+(\.\d+)?", c):
                        ctwa = c
                        break
                detections.append({"factory": cells[0], "point": (cells[1] if len(cells) > 1 else ""),
                                   "factor": factor, "ctwa": ctwa, "cste": "", "cme": ""})
        # 建构筑物表 (建筑物名称|占地|建面|层数)
        if "建构筑" in flat or ("建筑面积" in flat and "占地" in flat):
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                if len(cells) >= 2 and cells[0] and cells[0] != "建构筑物名称":
                    buildings.append({"name": cells[0], "area": cells[3] if len(cells) > 3 else "",
                                      "floor_area": cells[4] if len(cells) > 4 else ""})
        # 危害识别网格表 (评价单元|岗位|产品|工段|危害因素 或 岗位|产品类型|工艺/设备|涉及物料|危害因素)
        # 现状报告表20(18行)/表17/18/19 — 长兴真实危害网格, 直接提取
        hd0 = " ".join(str(c).replace("\n", "").strip() for c in tb[0])
        is_unit_grid = ("评价单元" in hd0 or "评价单元" in head0) and ("岗位" in hd0) and ("危害" in flat)
        is_post_grid = ("岗位" in hd0) and ("产品类型" in hd0) and ("职业病危害因素" in flat)
        if is_unit_grid or is_post_grid:
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                if not cells or not any(cells):
                    continue
                # 找单元列(评价单元)/岗位/产品/工段/危害因素
                # 表20: 序号|评价单元|岗位|产品|工段|危害因素 → unit=cells[1], post=cells[2], product=cells[3], stage=cells[4]
                # 表17/18/19: 岗位|产品类型|工艺/设备|涉及物料|作业方式|危害因素 → post=cells[0], product=cells[1], stage=cells[2]
                unit = ""
                post = ""
                product = ""
                stage = ""
                materials = ""
                if "评价单元" in (head0 or ""):
                    # 表头首行含"评价单元": 列序 = 序号/评价单元/岗位/产品/工段/...
                    actual = cells[1:] if (cells[0] == "序号" or cells[0].strip() in ("序号", "")) else cells
                    if len(actual) >= 1: unit = actual[0] if "评价单元" in head0 else ""
                    if len(actual) >= 2: post = actual[1]
                    if len(actual) >= 3: product = actual[2]
                    if len(actual) >= 4: stage = actual[3]
                elif "产品类型" in (head0 or ""):
                    post = cells[0]
                    product = cells[1] if len(cells) > 1 else ""
                    stage = cells[2] if len(cells) > 2 else ""
                    materials = cells[3] if len(cells) > 3 else ""
                # 危害因素列 (找含"危害"的最后一列/含 ; 、 , 的因素串)
                factors = ""
                for c in reversed(cells):
                    if c and re.search(r"(粉尘|苯|噪声|高温|酸|醇|酮|胺|树脂|烟尘|剂|微波|射线|工频)", c) and len(c) < 120 and c != (product or ""):
                        factors = c
                        break
                if not unit and not post and not product:
                    continue
                if factors or materials:
                    grid_row = {"unit": unit, "post": post, "product": product,
                                "stage": stage, "materials": materials, "factors": factors}
                    # 去重(unit+post+stage)
                    if grid_row not in hazard_grid_list:
                        hazard_grid_list.append(grid_row)
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
    if hazard_grid_list:
        out["hazard_grid"] = hazard_grid_list[:80]

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
    # 排除"评价内容主要包括"这类描述段(不是真正的措施/管理), 找实质措施段
    def _real_seg(seg):
        return seg and len(seg) > 50 and "评价内容主要包括" not in seg
    emg = ""
    for m in re.finditer(r"([^\n。]{25,220}(?:应急喷淋|洗眼器|急救药箱|应急救援设施|应急救援预案|正压式空气呼吸器|应急器材|化学事故应急)[^\n。]{15,280}[。])", text):
        seg = m.group(1).strip()
        if _real_seg(seg):
            emg = seg
            break
    if emg:
        out["emergency"] = emg
    mang = ""
    for m in re.finditer(r"([^\n。]{25,220}(?:职业健康监护档案|管理档案|管理制度|专(?:兼)?职|职业卫生专项经费|职业病危害防治责任|职业卫生管理机构|制定有《)[^\n。]{15,300}[。])", text):
        seg = m.group(1).strip()
        if _real_seg(seg):
            mang = seg
            break
    if mang:
        out["management"] = mang

    return out
