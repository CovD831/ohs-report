"""危害分析网格引擎 — 规则为主 + LLM兜底切工序

确定性边界:
  - 物质→危害因素: hazard_factor词典精确匹配(不靠LLM)
  - 设备→工序: 关键词规则表(不靠LLM)
  - 设备→物理因素: 设备类型规则(不靠LLM)
  - 岗位→工序: 关键词匹配(不靠LLM)
  - 健康影响: health_effect词典(不靠LLM)
  - LLM 只做: 工艺文本→工序列表(语义切分, 兜底)

产出: 危害分析网格 (评价单元/车间 → 工序 → 岗位 → 物料/中间产物 → 危害因素 → 接触方式)
"""
from __future__ import annotations


# ============ 设备名关键词 → 工序 (规则表, 跨行业通用) ============
_EQUIP_PROCESS = [
    # 化工
    (("反应釜", "反应器", "聚合釜", "酯化釜", "合成釜"), "反应"),
    (("搅拌", "混合机", "拌料机", "捏合机"), "混合"),
    (("蒸馏", "精馏", "提纯", "分离塔", "冷凝器"), "分离提纯"),
    (("干燥", "烘干", "烘箱", "干燥机"), "干燥"),
    (("粉碎", "破碎", "磨机", "研磨"), "粉碎"),
    # 焚烧/发电
    (("焚烧炉", "焚烧"), "焚烧"),
    (("锅炉", "余热锅炉", "汽包"), "热力"),
    (("汽轮机", "发电机组", "发电机"), "发电"),
    (("风机", "引风机", "送风机"), "通风"),
    # 机电/制造
    (("注塑机", "注塑"), "注塑"),
    (("挤出", "挤塑", "挤出机"), "挤出"),
    (("冲压", "冲床", "压力机"), "冲压"),
    (("数控", "CNC", "加工中心", "机床"), "机加工"),
    (("焊接", "焊机", "焊枪"), "焊接"),
    (("电镀", "镀槽", "电镀槽"), "电镀"),
    (("喷涂", "喷漆", "涂装", "喷粉"), "涂装"),
    (("装配", "组装线", "pack线"), "装配"),
    (("热处理", "淬火", "退火", "回火"), "热处理"),
    (("切割", "激光切割", "等离子切割"), "切割"),
    # 喷涂/清洗/包装通用
    (("清洗", "洗涤", "脱脂"), "清洗"),
    (("包装机", "灌装", "封口", "打包"), "包装"),
    (("空压机", "压缩空气"), "公辅"),
    (("冷却塔", "冷却水"), "公辅"),
]

# ============ 设备 → 物理因素 (规则) ============
_EQUIP_PHYS = [
    (("风机", "泵", "电机", "压缩机", "汽轮机", "发电机", "空压机", "减速机"), "噪声"),
    (("锅炉", "焚烧炉", "反应釜", "加热炉", "烘箱", "干燥机", "退火炉", "热处理"), "高温"),
    (("冲压", "冲床", "锻压", "振动", "破碎机", "粉碎机"), "振动"),
    (("发电机", "变压器", "配电", "高压", "变频器"), "工频电场"),
    (("炉", "灶", "烘烤"), "高温"),
    (("激光", "焊接", "切割", "电焊"), "紫外辐射"),
]

# ============ 岗位名关键词 → 工序 (用于网格岗位列) ============
_POST_PROCESS = [
    (("中控", "控制室", "操作工", "巡检", "值班"), "中控"),
    (("仓", "储", "料库", "原料库"), "仓储"),
]


def _match_kw(name: str, rules: list[tuple]) -> str:
    """按关键词规则匹配, 返回第一个命中的值"""
    name = (name or "").lower()
    for kws, val in rules:
        for kw in kws:
            if kw.lower() in name:
                return val
    return ""


def factors_from_material(conn, material: str) -> list[str]:
    """物质 → 危害因素 (hazard_factor词典精确匹配 + alias 别名)"""
    if not material:
        return []
    m = (material or "").strip()
    # 直接匹配 (物质名在 hazard_factor)
    rows = conn.execute(
        "SELECT name FROM hazard_factor WHERE name LIKE ? LIMIT 5", (m + "%",)).fetchall()
    factors = [r[0] for r in rows]
    # 别名匹配: oel_name=物料别名 → catalog_name=标准危害名
    if not factors:
        for r in conn.execute(
                "SELECT catalog_name FROM hazard_factor_alias WHERE oel_name LIKE ? LIMIT 5", (m + "%",)):
            if r[0]:
                factors.append(r[0])
        factors = list(dict.fromkeys(factors))[:5]
    return factors


def process_from_equipment(equipment: str) -> str:
    """设备名 → 工序"""
    return _match_kw(equipment, _EQUIP_PROCESS)


def physical_factors(equipment: str) -> list[str]:
    """设备名 → 物理因素"""
    v = _match_kw(equipment, _EQUIP_PHYS)
    return [v] if v else []


def process_from_post(post: str) -> str:
    """岗位名 → 工序/类别"""
    return _match_kw(post, _POST_PROCESS)


def health_effect(conn, factor: str) -> str:
    """危害因素 → 健康影响"""
    r = conn.execute("SELECT effect FROM health_effect WHERE factor LIKE ? LIMIT 1", (factor + "%",)).fetchone()
    return r[0] if r else ""


def _split_weighted(data) -> list[str]:
    """拆分物质/物料组分: 生活垃圾 → [生活垃圾, 硫化氢, 氨气, 甲硫醇] (从数据/规则拆)"""
    return [d for d in data if d]


def extract_units_from_text(process_text: str) -> list[str]:
    """从工艺文本提取评价单元 (如'划分为8个评价单元：XX、YY、ZZ')"""
    import re
    t = (process_text or "")
    # 匹配"划分为N个评价单元：A、B、C"
    m = re.search(r"划分为?\s*[\d一二三四五六七八九十]+个评价单元[：:]?\s*([^。]+)", t)
    if m:
        parts = re.split(r"[、，,；;]", m.group(1))
        return [p.strip() for p in parts if p.strip()]
    # 兜底: 短名词单元 (以单元/车间/工段/工序/系统结尾的短词组, ≤10字, 避免抓长句)
    # 用 [^\s，。；、,()（）] 限制不含标点, 且只取短片段
    units = re.findall(r"([\u4e00-\u9fffA-Za-z0-9]{1,10}(?:单元|车间|工段|工序|系统))", t)
    seen = []
    for u in units:
        # 过滤: 不以"的/由/在/为/及/和/等"作连接词的前缀碎片 (长句残留)
        if u and u not in seen and len(u) <= 12:
            # 剔除含连接词的长串 (如"注液废气由密闭设备注液机的抽风系统"含"的/由")
            if any(k in u for k in ("的", "由", "在", "为", "及", "和", "与", "或")):
                continue
            seen.append(u)
    return seen[:15]


def build_grid(conn, project: dict) -> list[dict]:
    """组装危害分析网格: 评价单元 → 工序 → 岗位 → 物料 → 危害因素 → 接触方式

    规则为主: harm 因子来自物料(hazard_factor) + 设备(物理因素), 不靠LLM建危害
    评价单元/工序 维度: 优先从工艺文本结构提取, 无则设备归类
    """
    import re
    # 优先用已提取的危害识别网格 (现状报告表20/17/18/19 真实数据, 规则提取)
    # 不再从叙述式工艺文本 extract_units_from_text 猜(会产出"划分评价单元"空壳)
    hg = project.get("hazard_grid") or []
    if hg:
        grid = []
        for g in hg:
            gf = g.get("factors") or ""
            fl = [x.strip() for x in re.split(r"[、;；]", str(gf)) if x.strip()] if isinstance(gf, str) else [str(x) for x in (gf or []) if str(x).strip()]
            mg = g.get("materials") or ""
            ml = [x.strip() for x in re.split(r"[、;；/]", str(mg)) if x.strip() and len(x.strip()) < 20] if isinstance(mg, str) else [str(x) for x in (mg or []) if str(x).strip()]
            grid.append({
                "unit": g.get("unit") or g.get("post") or "",
                "process": g.get("stage") or g.get("post") or "",
                "posts": [g.get("post")] if g.get("post") else [],
                "materials": ml,
                "factors": fl,
                "enclosed": "",
            })
        if grid:
            return grid
    eqs = project.get("equipment", []) or []
    mats = project.get("materials", []) or []
    staffs = project.get("staffing", []) or []
    proc_text = project.get("process_text", "") or ""

    # 设备驱动兜底 (生产单元聚合 + 辅助单元查aux_unit_hazard) — 跨行业通用, 不靠叙述工艺文本猜
    if eqs:
        g_eq = _grid_from_equipment(conn, eqs, mats)
        if g_eq:
            return g_eq

    # 1. 评价单元/工序: 从工艺文本提取, 或从设备归类
    units = extract_units_from_text(proc_text)
    if not units:
        # 从设备名归类工序
        seen_p = []
        for e in eqs:
            p = process_from_equipment(str(e))
            if p and p not in seen_p:
                seen_p.append(p)
        units = seen_p

    # 2. 每个评价单元 → 设备(属该单元) → 岗位 → 物料 → 危害
    grid = []
    # 设备→物料关联 (equipment_material库: 设备名→对应物料/中间产物)
    eq_mat = {}
    try:
        for r in conn.execute("SELECT equipment, material FROM equipment_material"):
            eq_mat[str(r[0]).strip()] = str(r[1] or "")
    except Exception:
        eq_mat = {}

    for unit in units:
        # 单元核心词 (去'单元/车间/系统/工段'后缀, 用于匹配设备名)
        u_core = unit.rstrip("单元车间系统工段")
        # 评价单元类型 → 典型危害规则表 (准确优先, 明确映射)
        unit_factors = _UNIT_FACTORS.get(u_core, [])
        # 规则表没覆盖的(新行业如化工树脂/3D打印/锂电池), 用报告经验反查(按行业筛选)
        if not unit_factors:
            try:
                from web.report_experience import unit_experience
                ind = project.get("industry", "")
                unit_factors = unit_experience(unit, ind) or unit_experience(u_core, ind)
            except Exception:
                unit_factors = []
        # 该单元的设备 (设备名含单元核心词, 或工序规整后==单元)
        u_eqs = []
        for e in eqs:
            es = str(e)
            if unit in es or (u_core and u_core in es) or _match_kw(es, [(unit, unit), (u_core, u_core)]):
                u_eqs.append(es)
        if not u_eqs:
            u_eqs = [str(e) for e in eqs if process_from_equipment(str(e)) == unit or
                     (u_core and process_from_equipment(str(e)) == u_core)]
        # 该单元的物料: 从单元设备→物料关联 + 单元名匹配物料
        u_mats = []
        for es in u_eqs:
            m = eq_mat.get(es, "")
            if m:
                for part in str(m).replace("物料：", "").replace("槽内：", "").replace("管程：", "").split("/"):
                    part = part.replace("壳程", "").strip().split("、")[0] if "、" in part else part.strip()
                    part = part.strip()
                    if part and part not in u_mats and len(part) < 20:
                        u_mats.append(part)
        for mm in mats:
            mn = mm.get("name", "") if isinstance(mm, dict) else str(mm)
            if unit in mn and mn not in u_mats:
                u_mats.append(mn)
        # 危害因素: 先取评价单元规则表(准确映射) + 该单元物料→危害 + 该单元设备→物理因素
        factors = [f for f in unit_factors]  # 单元类型典型危害(基础)
        for mn in u_mats:
            for f in factors_from_material(conn, mn):
                if f not in factors and f not in _PHYS_ONLY:
                    factors.append(f)
        for es in u_eqs:
            for pf in physical_factors(es):
                if pf not in factors:
                    factors.append(pf)
        # 岗位: 该单元匹配定员
        u_posts = []
        for st in staffs:
            sv = (st.get("dept", "") or "") + (st.get("post", "") or "") if isinstance(st, dict) else str(st)
            if unit in sv or (u_core and u_core in sv):
                u_posts.append(st.get("post", "") if isinstance(st, dict) else str(st))
        mat_names = u_mats or ["—"]
        grid.append({
            "unit": unit,
            "process": unit,
            "posts": u_posts or ["各岗位"],
            "materials": mat_names,
            "factors": factors,
            "enclosed": _detect_enclosed(u_eqs),
        })
    return grid


# 评价单元核心词 → 典型危害因素 (准确映射, 不靠模糊设备兜底)
# 物理因素单列(由设备物理规则补充), 这里主记化学/粉尘因素
_PHYS_ONLY = {"噪声", "高温", "工频电场", "振动", "紫外辐射"}
_UNIT_FACTORS = {
    # 垃圾/固废焚烧
    "垃圾贮存": ["硫化氢", "氨", "甲硫醇", "粉尘"],
    "垃圾焚烧": ["一氧化碳", "硫化氢", "二氧化硫", "氮氧化物", "氯化氢", "粉尘"],
    "余热锅炉": [],  # 以物理因素为主
    "汽轮发电机": [],
    "灰渣处理": ["粉尘"],
    "烟气净化": ["活性炭粉尘", "氢氧化钙粉尘"],
    "渗滤液": ["硫化氢", "氨", "甲硫醇"],
    "水处理": ["次氯酸钠", "氨", "硫化氢"],
    "辅助生产": [],
    # 化工
    "反应": ["化学有害因素"],
    "分离提纯": ["化学有害因素"],
    "干燥": ["粉尘"],
    "粉碎": ["粉尘"],
    "混合": ["粉尘", "化学有害因素"],
    # 机械制造
    "注塑": ["粉尘", "化学有害因素"],
    "挤出": ["粉尘", "化学有害因素"],
    "焊接": ["电焊烟尘", "锰及其化合物", "臭氧", "紫外辐射"],
    "涂装": ["苯系物", "甲苯", "二甲苯", "粉尘"],
    "机加工": ["金属粉尘", "油雾", "噪声"],
    "电镀": ["铬酸雾", "氰化物", "盐酸雾"],
    "清洗": ["化学有害因素"],
    "切割": ["金属粉尘", "噪声", "紫外辐射"],
}


def _detect_enclosed(eqs: list) -> str:
    """密闭性判断: 设备名含'密闭/密封/负压'→密闭; 含'敞开/敞口'→敞开; 否则空"""
    if not eqs:
        return ""
    s = " ".join(str(e) for e in eqs)
    if "密闭" in s or "密封" in s or "负压" in s:
        return "密闭"
    if "敞开" in s or "敞口" in s or "开放" in s:
        return "敞开式"
    return ""


def _grid_from_equipment(conn, eqs: list, mats: list) -> list[dict]:
    """设备驱动兜底: 生产单元(=设备聚合成'XX生产单元') + 辅助单元(查aux_unit_hazard补危害)

    无提取的hazard_grid(无现状报告表的新项目/其他行业)时用。
    判定: 设备名命中 aux_unit_hazard.keywords → 辅助单元(查表得危害);
          否则 process_from_equipment 得生产工序(非'公辅') → 生产单元"工序+生产单元"。
    跨行业通用: 反应釜→生产/反应, 注塑机→生产/注塑, 配电室→辅助/工频电场(任何行业一致)。"""
    # 读 aux_unit_hazard (辅助单元关键词→危害)
    aux = []  # [(unit, keywords_list, factors_list)]
    try:
        for r in conn.execute("SELECT unit, keywords, factors FROM aux_unit_hazard"):
            kws = [k.strip() for k in str(r[1] or "").split(",") if k.strip()]
            facs = [f.strip() for f in str(r[2] or "").split(",") if f.strip()]
            aux.append((r[0], kws, facs))
    except Exception:
        aux = []
    # 设备→物料 (equipment_material) 供物料列
    eq_mat = {}
    try:
        for r in conn.execute("SELECT equipment, material FROM equipment_material"):
            eq_mat[str(r[0]).strip()] = str(r[1] or "")
    except Exception:
        eq_mat = {}

    prod = {}  # 工序 → 设备列表
    auxu = {}  # 辅助单元名 → factors集合
    for e in str(eqs) if isinstance(eqs, (set, tuple)) else eqs:
        es = str(e)
        if not es:
            continue
        # 判定辅助单元
        hit = None
        for uname, kws, facs in aux:
            if any(kw in es for kw in kws):
                hit = (uname, facs)
                break
        if hit:
            auxu.setdefault(hit[0], [])
            for f in hit[1]:
                if f not in auxu[hit[0]]:
                    auxu[hit[0]].append(f)
            continue
        # 生产设备 → 工序
        p = process_from_equipment(es)
        if p and p != "公辅":
            prod.setdefault(p, []).append(es)

    grid = []
    # 生产单元: 每工序一个 "XX生产单元"
    for p, eqlist in prod.items():
        facs = []
        for e in eqlist:
            for pf in physical_factors(e):
                if pf not in facs:
                    facs.append(pf)
        mats_u = []
        for e in eqlist:
            m = eq_mat.get(e, "")
            if m:
                parts = [x.strip() for x in str(m).replace("物料：", "").replace("槽内：", "").replace("管程：", "").split("/") if x.strip() and len(x.strip()) < 20]
                for pa in parts:
                    if pa not in mats_u:
                        mats_u.append(pa)
        # 物料→化学危害 (factors_from_material: 苯乙烯/乙二醇...查hazard_factor)
        for mn in mats_u + [str(m.get("name", "")) if isinstance(m, dict) else str(m) for m in (mats or [])]:
            if not mn:
                continue
            for fm in factors_from_material(conn, mn):
                if fm not in facs:
                    facs.append(fm)
        grid.append({"unit": p + "生产单元", "process": p, "posts": [],
                     "materials": mats_u[:4], "factors": facs, "enclosed": _detect_enclosed(eqlist)})
    # 辅助单元: 查 aux_unit_hazard 得危害
    for uname, facs in auxu.items():
        grid.append({"unit": uname, "process": "辅助", "posts": [],
                     "materials": [], "factors": list(facs), "enclosed": ""})
    return grid
