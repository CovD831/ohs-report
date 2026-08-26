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

    # ===== 设备 (设备表: 车间|序号|位号|设备名称|规格型号|数量|材质 → 设备名 + 完整列) =====
    eq, eq_detail = [], []
    eq_dict = []  # equipment_detail: {name/位号/规格/数量/材质/车间}
    for tb in tables:
        flat = " | ".join(str(c).replace("\n", "") for r in tb for c in r)
        if not ("设备名称" in flat and "位号" in flat):
            continue
        head = [str(c).replace("\n", " ").strip() for c in tb[0]]
        def _ec(kws):
            for i, h in enumerate(head):
                for kw in kws:
                    if kw in h:
                        return i
            return -1
        c_pos = _ec(["位号"])
        c_spec = _ec(["规格", "型号"])
        c_qty = _ec(["数量"])
        c_mat = _ec(["材质"])
        c_dept = _ec(["车间"])
        for row in tb[1:]:
            cells = [str(c).replace("\n", " ").strip() for c in row]
            if not cells or any("设备名称" == c for c in cells):
                continue
            # 设备名称列 (找含"釜/槽/罐/泵/塔/机/炉/器/线"的单元格)
            eqname = ""
            for c in cells:
                if re.search(r"(反应釜|稀释槽|稀释釜|调整槽|中间槽|储罐|储槽|泵|冷凝器|冷凝|塔|风机|锅炉|搅拌|砂磨|分散|包装线|生产线|热媒)", c) and len(c) <= 20:
                    eqname = c
                    break
            if not eqname:
                continue
            if eqname and eqname not in eq_detail:
                eq_detail.append(eqname)
            ed = {"name": eqname}
            def _g(idx):
                return cells[idx] if (idx >= 0 and idx < len(cells)) else ""
            ed["位号"] = _g(c_pos)
            ed["spec"] = _g(c_spec)
            ed["qty"] = _g(c_qty)
            ed["材质"] = _g(c_mat)
            ed["车间"] = _g(c_dept)
            if not any(x.get("name") == eqname and x.get("spec") == ed["spec"] for x in eq_dict):
                eq_dict.append(ed)
        if len(eq_detail) >= 40:
            break
    out["equipment"] = eq_detail[:40]
    out["equipment_detail"] = eq_dict[:40]

    # ===== 原辅材料 (通用: 只从"原辅料表"取完整列: 名称/规格/年用量/最大储量/物态/储存地点) =====
    # 表4: 序号 物料名称 目录序号 规格 年用量t/a 最大储量t 物态 包装 储存地点 储存条件 → 按表头定位各列
    mats = []
    seen_m = set()
    for tb in tables:
        if not tb or not tb[0]:
            continue
        head = [str(c).replace("\n", " ").strip() for c in tb[0]]
        # 通用列定位 (任何行业原辅料表有物料名称+年用量+储存地点等列)
        def _col(kws):
            for i, h in enumerate(head):
                for kw in kws:
                    if kw in h:
                        return i
            return -1
        mic = _col(["原辅材料名称", "物料名称", "原料名称"])
        if mic < 0:
            continue
        c_spec = _col(["规格", "型号"])
        c_year = _col(["年用量", "年耗量", "年消耗", "年用量t", "年耗量t"])
        c_stk = _col(["最大储量", "最大储存量", "最大贮"])
        c_state = _col(["物态", "形态"])
        c_loc = _col(["储存地点", "存放地点", "存储地点", "存于"])
        c_cas = _col(["CAS"])
        for row in tb[1:]:
            if mic >= len(row):
                continue
            v = str(row[mic]).replace("\n", " ").strip()
            if not v or v == "None":
                continue
            if re.match(r"^(序号|物料名称|原辅材料名称|原料名称|合计|小计|备注)", v):
                continue
            v = re.sub(r"[\s，]", "", v)
            if not v or len(v) > 30 or v in seen_m:
                continue
            m = {"name": v}
            if c_spec >= 0 and c_spec < len(row):
                m["规格"] = str(row[c_spec]).replace("\n", " ").strip()
            if c_year >= 0 and c_year < len(row):
                m["年用量"] = str(row[c_year]).replace("\n", " ").strip()
            if c_stk >= 0 and c_stk < len(row):
                m["最大储量"] = str(row[c_stk]).replace("\n", " ").strip()
            if c_state >= 0 and c_state < len(row):
                m["物态"] = str(row[c_state]).replace("\n", " ").strip()
            if c_loc >= 0 and c_loc < len(row):
                m["储存地点"] = str(row[c_loc]).replace("\n", " ").strip()
            if c_cas >= 0 and c_cas < len(row):
                m["cas"] = str(row[c_cas]).replace("\n", " ").strip()
            seen_m.add(v)
            mats.append(m)
        if len(mats) >= 45:
            break
    out["materials"] = mats[:45]

    # ===== 产品 (通用: 只匹配真产品表: 表头含'名称'+'主要成分/年产量', 排除'物料名称'表/技术指标表) =====
    # 产品表(如表5): 名称|名称|主要成分|年产量|最大储量|物态 → 产品名=第一名称列, 类型=第二名称列, 年产量=产量列
    # 关键: 排除 单位/万吨/物料名称 等非产品 (之前pic兜底误取技术指标表/物料表)
    prods = []
    _NOISE = ("产品", "中间产品", "新增不饱和树脂", "新增产品", "主要产品", "副产品", "产品方案", "小计", "合计", "产品名称",
              "废气", "废水", "废渣", "三废", "污染物", "None", "扩产前", "扩产", "未命名", "原有", "改建",
              "单位", "万吨", "万千瓦时", "万立方米", "吨", "物料名称", "原料名称")
    for tb in tables:
        if not tb or not tb[0]:
            continue
        head = [str(c).replace("\n", " ").strip() for c in tb[0]]
        flat_head = " ".join(head)
        # 产品表特征: 有'名称' + (产品名称/主要成分/年产量 任一), 且 非物料名称表
        if "物料名称" in flat_head or "原料名称" in flat_head:
            continue  # 原辅料表, 不是产品表
        is_prod_tbl = ("产品名称" in flat_head or "产品" in flat_head) and ("年产量" in flat_head or "产量" in flat_head or "变化量" in flat_head or "产能" in flat_head)
        is_mixed_tbl = ("名称" in flat_head and "主要成分" in flat_head and ("年产量" in flat_head or "最大储量" in flat_head))
        if not (is_prod_tbl or is_mixed_tbl):
            continue
        # 产品名列: 优先'产品名称', 否则第一'名称'列(混合表 cells[1])
        pic = -1
        for i, h in enumerate(head):
            if "产品名称" in h:
                pic = i
                break
        if pic < 0:
            for i, h in enumerate(head):
                if h.replace(" ", "") == "名称":
                    pic = i
                    break
        if pic < 0:
            continue
        # 类型列(混合表第二'名称'), 年产量列
        c_type = -1
        c_out = -1
        for i, h in enumerate(head):
            hs = h.replace(" ", "")
            if hs == "名称" and i > pic and c_type < 0:
                c_type = i
            if "年产量" in h or "产量" in h or "吨/年" in h:
                c_out = i
        for row in tb[1:]:
            if pic >= len(row):
                continue
            c = str(row[pic]).replace("\n", " ").strip()
            if not c:
                continue
            c2 = re.sub(r"[（(].*?[)）]", "", c).strip()
            c2 = re.sub(r"^\d+(\.\d+)?", "", c2).strip()
            c2 = c2.replace("吨/年", "").replace("产品", "").strip()
            c2 = re.sub(r"[\s、,，/]", "", c2)
            c2 = re.sub(r"\d+(\.\d+)?", "", c2).strip()
            # 类型拼进产品名 (通用型/复合材料型 → 不饱和聚酯树脂(通用型)); 类型==产品名则不加
            if c_type >= 0 and c_type < len(row):
                typ = str(row[c_type]).replace("\n", " ").strip()
                base0 = re.sub(r"[（(].*?[)）]", "", c).strip()
                if typ and typ not in _NOISE and len(typ) <= 12 and typ != base0 and typ not in c2:
                    c2 = c2 + "（" + typ + "）"
            if re.search(r"(工艺|生产工序|生产方案|方案|生产线)", c2) or len(c2) > 20:
                continue
            if not c2 or c2 in _NOISE or any(n in c2 for n in ("废气", "废水", "废渣", "污染物", "单位", "万吨")):
                continue
            if c2 in [p.get("name") for p in prods]:
                continue
            # base/带类型 去重: 若已有"产品(类型)", 删纯base; 若纯base已有带类型, 跳过
            b = c2.split("（")[0]
            if "（" in c2:
                if b in [p.get("name") for p in prods]:
                    prods[:] = [p for p in prods if p.get("name") != b]
            elif any(p.get("name", "").split("（")[0] == c2 for p in prods):
                continue
            p = {"name": c2}
            if c_out >= 0 and c_out < len(row):
                p["output"] = str(row[c_out]).replace("\n", " ").strip()
            prods.append(p)
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
        # 定员表 (部门|岗位|总人数|班制) — 兼容表头变体: 岗位/工种, 按表头定位列
        if ("岗位" in head0 or "工种" in head0) and ("总人数" in flat or "人数" in flat):
            hcells2 = [str(c).replace("\n", " ").strip() for c in tb[0]]
            def _lc(kws):
                for i, h in enumerate(hcells2):
                    for kw in kws:
                        if kw in h:
                            return i
                return -1
            i_post = _lc(["岗位", "工种"])
            i_cnt = _lc(["总人数", "人数"])
            i_dept = _lc(["车间", "部门", "工作区域", "工段"])
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                def _gg(idx):
                    return cells[idx] if (idx >= 0 and idx < len(cells)) else ""
                post = _gg(i_post)
                cnt = _gg(i_cnt)
                dept = _gg(i_dept)
                if not post and not cnt:
                    continue
                if not cnt:
                    cnt = next((c for c in reversed(cells) if re.fullmatch(r"\d+", c)), "")
                if post and cnt:
                    nh = {"post": post, "count": cnt, "dept": dept}
                    if nh["post"] not in [s.get("post") for s in staffing]:
                        staffing.append(nh)
        # 班制 (工作班制列)
        if "工作班制" in flat:
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                if len(cells) >= 5 and any("班" in c for c in cells):
                    shifts.append({"system": cells[4] if len(cells) > 4 else "", "post": cells[1] if len(cells) > 1 else ""})
        # 检测表 (采样车间|采样点|粉尘/毒物|检测结果 或 工种|检测地点|...|检测结果mg/m3)
        # 兼容表头变体: 采样/检测地点/工种 + 检测结果/mg
        if (("采样" in head0) or ("检测地点" in head0) or ("工作场所" in head0) or ("工种" in head0)) \
                and ("检测结果" in flat or "mg/m3" in flat or "mg" in flat or "CTWA" in flat):
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
                    if c and len(c) <= 22 and re.search(r"(苯乙烯|苯|粉尘|滑石|甲苯|二甲苯|乙二醇|丙二醇|甲醇|丙醇|丁酮|丙酮|乙酸|乙酯|氢氧化|矽|游离|锰|臭氧|氮氧化物|硫酸|镍|铬|锑|炭黑|白炭黑|电焊烟尘|矽尘|树脂|铅|汞|噪声|高温|振动|氟|氟化物|氯|氯化|锂|氢氟酸|盐酸|碱|氨|碳酸钠|纯碱|氢氟酸|氟化氢|六氟磷酸锂)", c) and not re.match(r"^\d", c):
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
        # 建构筑物表 (按表头定位列: 功能区/名称/火灾危险/耐火等级/层数/占地/建面)
        if "建构筑" in flat or ("建筑面积" in flat and "占地" in flat):
            head = [str(c).replace("\n", " ").strip() for c in tb[0]]
            def _bc(kws):
                for i, h in enumerate(head):
                    for kw in kws:
                        if kw in h:
                            return i
                return -1
            c_fun = _bc(["功能区", "区域"])
            c_name = _bc(["建构筑物名称"])  # 只精确建构筑物名称, 不用宽松'名称'(避免申请报告表头不同把功能区当name)
            if c_name < 0:
                continue  # 表头无"建构筑物名称"列 → 非标准建构筑物表, 不提取(避免污染)
            c_fire = _bc(["火灾危险", "火灾危险性"])
            c_arch = _bc(["耐火等级", "耐火"])
            c_floor = _bc(["层数"])
            c_area = _bc(["占地面积", "占地"])
            c_barea = _bc(["建筑面积", "建面"])
            c_ht = _bc(["高度"])
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                name = cells[c_name] if (c_name >= 0 and c_name < len(cells)) else (cells[0] if cells else "")
                if not name or name == "建构筑物名称":
                    continue
                b = {"name": name}
                if c_fun >= 0 and c_fun < len(cells):
                    b["功能区"] = cells[c_fun]
                if c_fire >= 0 and c_fire < len(cells):
                    b["火灾危险类别"] = cells[c_fire]
                if c_arch >= 0 and c_arch < len(cells):
                    b["耐火等级"] = cells[c_arch]
                if c_floor >= 0 and c_floor < len(cells):
                    b["floors"] = cells[c_floor]
                if c_area >= 0 and c_area < len(cells):
                    b["area"] = cells[c_area]
                if c_barea >= 0 and c_barea < len(cells):
                    b["floor_area"] = cells[c_barea]
                if c_ht >= 0 and c_ht < len(cells):
                    b["height"] = cells[c_ht]
                if b["name"] not in [x.get("name") for x in buildings]:
                    buildings.append(b)
        # 危害识别网格表 (评价单元|岗位|产品|工段|危害因素 或 岗位|产品类型|工艺/设备|涉及物料|危害因素)
        # 现状报告表20(18行)/表17/18/19 — 长兴真实危害网格, 直接提取
        hd0 = " ".join(str(c).replace("\n", "").strip() for c in tb[0])
        is_unit_grid = (("评价单元" in hd0) or ("评价单元" in head0)) and ("危害" in flat)
        is_post_grid = (("岗位" in hd0) or ("工种" in hd0)) and (("工序" in hd0) or ("产品类型" in hd0) or ("工艺" in hd0) or ("工段" in hd0)) and (("职业病危害因素" in flat) or ("危害" in flat))
        if is_unit_grid or is_post_grid:
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                if not cells or not any(cells):
                    continue
                # 按表头定位列 (通用): 评价单元/岗位|工种/工序|工段/产品/危害因素/物料|介质
                # 兼容 长兴表20(序号|评价单元|岗位|产品|工段|危害) + 新泰T71(序号|评价单元|工序|危害) + 新泰T75(序号|工种|工序|人数|危害)
                hcells = [str(c).replace("\n", " ").strip() for c in tb[0]]
                def _gc(kws):
                    for i, h in enumerate(hcells):
                        for kw in kws:
                            if kw in h:
                                return i
                    return -1
                i_unit = _gc(["评价单元"])
                i_post = _gc(["岗位", "工种"])
                i_stage = _gc(["工序", "工段"])
                i_prod = _gc(["产品"])
                i_mat = _gc(["物料", "介质", "中间产物"])
                i_fac = _gc(["接触职业病危害", "职业病危害因素", "危害因素", "危害"])
                def _g(idx):
                    return cells[idx] if (idx >= 0 and idx < len(cells)) else ""
                unit = _g(i_unit)
                post = _g(i_post)
                stage = _g(i_stage)
                product = _g(i_prod)
                materials = _g(i_mat)
                factors = _g(i_fac)
                # 危害因素兜底: 倒序找含危害特征词的最后列
                if not factors:
                    for c in reversed(cells):
                        if c and re.search(r"(粉尘|苯|噪声|高温|酸|醇|酮|胺|树脂|烟尘|剂|微波|射线|工频|氟|氯|锂|碱|氨|矽)", c) and len(c) < 120 and c != (product or ""):
                            factors = c
                            break
                if not (unit or post or product):
                    continue
                if factors or materials:
                    grid_row = {"unit": unit, "post": post, "product": product,
                                "stage": stage, "materials": materials, "factors": factors}
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

    # ===== 应急物资表 (类别|名称|数量|放置点位 — 表56) 通用: 表头含(放置点位+类别+名称) =====
    emg_s = []
    for tb in tables:
        if not tb or not tb[0]:
            continue
        head = [str(c).replace("\n", " ").strip() for c in tb[0]]
        hjoins = " ".join(head)
        if "放置点位" in hjoins and "类别" in hjoins and "名称" in hjoins:
            for row in tb[1:]:
                cells = [str(c).replace("\n", " ").strip() for c in row]
                if len(cells) >= 2 and cells[1] and cells[1] not in ("名称", "类别"):
                    emg_s.append({"类别": cells[0], "名称": cells[1],
                                  "数量": cells[2] if len(cells) > 2 else "",
                                  "点位": cells[3] if len(cells) > 3 else ""})
            break
    if emg_s:
        out["emergency_supplies"] = emg_s[:60]

    return out
