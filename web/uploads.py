"""材料上传存储 — 按机构资料收集单分类 (2026-08 重构)

收集单 16 类资料 → 报告数据槽。取代旧的附录A A2x 命名。

目录: data/materials/<project_id>/<类别key>_<类别名>/<原文件名>
类别 (机构资料收集单 + GBZ/T 196—2025 附录A):
  C1  项目批文      项目建议书/可研/备案/营业执照/立项
  C2  项目概况      投资/面积/性质/行业/产能/辐射源项/基本情况
  C3  原有项目     改扩建: 原有项目投产时间/产量/职业卫生情况
  C4  公辅工程      给排水/压缩空气/三废/水电气用量来源
  C5  总平面图      总平面布置图/竖向布置图/规划图/区域位置图
  C6  设备清单      设备名称/型号/数量/岗位/工艺/设施一览
  C7  生产工艺      工艺流程图/工艺简介/操作方式/生产方式
  C8  原辅材料      原辅料清单/用量/储存/MSDS/产品/中间品
  C9  产品产量      产品种类/年产量/产能
  C10 岗位定员      车间/岗位/作业点/人数/班制/劳动组织
  C11 辅助用室      卫生间/食堂/淋浴/休息室/位置/容量
  C12 采光通风      采光/通风/照明/建筑卫生
  C13 防护措施      拟采取防护设施/参数/位置/数量
  C14 个人防护用品  名称/型号/用量/更换周期/PPE
  C15 应急救援      应急预案/器材/药品/急救箱/演练
  C16 管理经费      职业卫生管理机构/人员/防治经费/保健制度
上传后: 自动导入 (设备/检测/工艺文本) 已有解析器
"""
import shutil
import uuid
from pathlib import Path

MATERIAL_ROOT = Path(__file__).resolve().parent.parent / "data" / "materials"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# 类别定义 (窗口顺序) — 对应机构资料收集单
CATEGORIES = [
    {"key": "C1", "name": "项目批文", "desc": "项目建议书/可行性研究/备案通知书/营业执照/立项文件", "ext": [".doc", ".docx", ".pdf", ".txt", ".json"]},
    {"key": "C2", "name": "项目概况", "desc": "项目名称/投资/面积/性质/行业/产能/辐射源项/基本情况", "ext": [".doc", ".docx", ".pdf", ".txt", ".json"]},
    {"key": "C3", "name": "原有项目", "desc": "改扩建: 原有项目投产时间/产量/职业卫生情况", "ext": [".doc", ".docx", ".pdf", ".txt", ".json"]},
    {"key": "C4", "name": "公辅工程", "desc": "给排水/压缩空气/三废处理/水电气用量来源", "ext": [".doc", ".docx", ".pdf", ".txt"]},
    {"key": "C5", "name": "总平面图", "desc": "总平面布置图/竖向布置图/规划图/区域位置图", "ext": [".dwg", ".pdf", ".png", ".jpg"]},
    {"key": "C6", "name": "设备清单", "desc": "设备名称/型号/数量/岗位/工艺/设施一览", "ext": [".xlsx", ".csv", ".doc", ".docx"]},
    {"key": "C7", "name": "生产工艺", "desc": "工艺流程图/工艺简介/操作方式/生产方式", "ext": [".doc", ".docx", ".pdf", ".txt"]},
    {"key": "C8", "name": "原辅材料", "desc": "原辅料清单/用量/储存/MSDS/成分", "ext": [".xlsx", ".csv", ".doc", ".docx", ".pdf"]},
    {"key": "C9", "name": "产品产量", "desc": "产品种类/年产量/产能", "ext": [".xlsx", ".csv", ".doc", ".docx"]},
    {"key": "C10", "name": "岗位定员", "desc": "车间/岗位/作业点/人数/班制/劳动组织", "ext": [".xlsx", ".csv", ".doc", ".docx"]},
    {"key": "C11", "name": "辅助用室", "desc": "卫生间/食堂/淋浴/休息室/位置/容量", "ext": [".doc", ".docx", ".pdf", ".txt"]},
    {"key": "C12", "name": "采光通风", "desc": "采光/通风/照明/建筑卫生", "ext": [".doc", ".docx", ".pdf", ".txt"]},
    {"key": "C13", "name": "防护措施", "desc": "拟采取防护设施/参数/位置/数量", "ext": [".doc", ".docx", ".pdf", ".txt"]},
    {"key": "C14", "name": "个人防护用品", "desc": "名称/型号/用量/更换周期/PPE配备", "ext": [".doc", ".docx", ".xlsx", ".csv"]},
    {"key": "C15", "name": "应急救援", "desc": "应急预案/器材/药品/急救箱/演练", "ext": [".doc", ".docx", ".pdf", ".txt"]},
    {"key": "C16", "name": "管理经费", "desc": "职业卫生管理机构/人员/防治经费/保健制度", "ext": [".doc", ".docx", ".pdf", ".txt"]},
    {"key": "C17", "name": "类比检测", "desc": "类比工程检测报告/职业卫生现场检测资料/类比企业检测数据", "ext": [".xlsx", ".csv", ".doc", ".docx", ".pdf"]},
]

# 兼容: 旧附录A文件名(A2x) → 重映射到收集单类别
_OLD_A2X_MAP = {
    "A1": "C1", "A2a": "C8", "A2b": "C7", "A2c": "C6",
    "A2d": "C10", "A2e": "C17", "A2f": "C13", "A2g": "C15", "A2h": "C5",
}

# 收集单类别 → 影响的报告章节 (缺失该资料会削弱/无法生成的章节)
# 用于"缺什么标注影响哪"的错误兜底
CATEGORY_IMPACTS = {
    "C1": ("第1章 总论、第3章 工程概况", "项目背景/立项依据"),
    "C2": ("第1章 总论、第3章 工程概况", "项目基本情况"),
    "C3": ("第2章 现有企业概况", "原有项目情况(改扩建)"),
    "C4": ("第3章 3.1 公辅工程、3.7 建筑卫生学", "公辅工程"),
    "C5": ("第3章 3.2 选址、3.3 总体布局、3.7 建筑卫生学", "总平面布置/建筑卫生"),
    "C6": ("第3章 3.6 生产设备", "设备清单(核心)"),
    "C7": ("第3章 3.5 生产工艺、第5章 5.1 识别", "生产工艺流程(核心)"),
    "C8": ("第3章 3.4 原辅材料、第5章 5.1 识别", "原辅材料(核心)"),
    "C9": ("第3章 3.4 产品、3.5 生产工艺", "产品产量"),
    "C10": ("第3章 3.1 岗位定员、第9章 9.1", "岗位定员"),
    "C11": ("第3章 3.8 辅助用室", "辅助用室"),
    "C12": ("第3章 3.7 建筑卫生学", "采光通风照明"),
    "C13": ("第6章 防护设施", "防护措施(核心)"),
    "C14": ("第8章 个人防护用品", "个人防护用品"),
    "C15": ("第7章 应急救援", "应急救援"),
    "C16": ("第9章 职业卫生管理/经费", "职业卫生管理/经费"),
    "C17": ("第4章 类比调查分析、第5章 5.4 危害程度", "类比检测数据(支撑危害程度分析)"),
}
# 核心类别 (缺失则显著影响报告完整度, 视为"关键缺漏")
CORE_CATEGORIES = {"C1", "C2", "C6", "C7", "C8", "C10", "C13", "C17"}


def coverage_report(pid: str, check_categories: list[str] | None = None, project: dict | None = None) -> dict:
    """资料覆盖度检查 — 双层兜底: ①缺什么文件(类别) ②缺什么文件的什么字段

    check_categories: 已收集到的类别 key 列表 (None 则从项目材料目录读取)
    project: 提取后的项目 data (提供则加字段级校验)
    返回: {covered, missing, completeness, core_missing, field_missing, filed_completeness}
    """
    if check_categories is None:
        check_categories = {m.get("category") for m in list_materials(pid)} - {"uncat"}
    cats = [c["key"] for c in CATEGORIES]
    covered = [c for c in cats if c in check_categories]
    missing = []
    for c in cats:
        if c not in check_categories:
            meta = next((x for x in CATEGORIES if x["key"] == c), {})
            ch, why = CATEGORY_IMPACTS.get(c, ("", "该资料"))
            missing.append({"cat": c, "name": meta.get("name", c),
                            "impact": ch, "why": why,
                            "core": c in CORE_CATEGORIES})
    completeness = round(len(covered) / len(cats) * 100)
    result = {"covered": covered, "missing": missing,
              "completeness": completeness,
              "core_missing": [m for m in missing if m["core"]]}
    # 字段级校验 (project 提供时)
    if project:
        from web.project_schema import validate_project
        fv = validate_project(project)
        result["field_missing"] = fv["required_missing"]
        result["field_partial"] = fv["partial"]
        result["field_completeness"] = fv["completeness"]
    return result


def classify_file(filename: str, content: bytes = b"") -> str:
    """按文件名 + 内容关键词 推断资料类别, 返回类别 key 或 'unknown'(进待定区)"""
    name = filename.lower()
    # 旧 A2x 文件名重映射 (兼容历史材料) — 匹配完整前缀如 A2c/A2e/A2a
    import re
    m = re.match(r"^(A[12][a-h]|A[1-9])", filename, re.I)
    if m and m.group(1) in _OLD_A2X_MAP:
        return _OLD_A2X_MAP[m.group(1)]

    # 收集单类别关键词 (多个命中按优先级: 越具体越优先)
    rules = [
        # (关键词组, 类别key)
        (("类比检测", "类比工程检测", "类比企业检测", "类比项目检测"), "C17"),
        (("检测数据", "现场检测", "检测报告", "职业病危害因素检测", "汇总监测", "ctwa", "cstel", "cmac"), "C17"),
        (("营业执照", "项目建议书", "可研", "可行性研究", "备案通知", "备案证", "备案公告", "立项", "批复"), "C1"),
        (("项目概况", "基本情况", "投资总额", "建筑面积", "产能", "辐射源", "行业类别", "建设规模"), "C2"),
        (("原有项目", "现有企业", "现状评价", "原有厂房", "原有产能"), "C3"),
        (("公辅", "给水", "排水", "压缩空气", "三废", "水用量", "电用量", "气用量"), "C4"),
        (("总平面", "竖向布置", "规划图", "区域位置", "平面布置", "设计图", "布置图", "dwg"), "C5"),
        (("设备清单", "设备明细", "设备一览", "机器", "装置清单", "设备型号"), "C6"),
        (("工艺流程", "工艺说明", "工艺简介", "生产方式", "操作方式", "工艺流程"), "C7"),
        (("原辅材料", "原辅料", "原料清单", "物料清单", "成分", "msds", "用量"), "C8"),
        (("产品产量", "产品种类", "年产量", "产品方案", "产能表"), "C9"),
        (("岗位定员", "劳动定员", "定员表", "岗位表", "人员编制", "班制"), "C10"),
        (("辅助用室", "卫生间", "食堂", "淋浴", "休息室", "更衣", "卫生辅助用室"), "C11"),
        (("职业健康检查", "职业健康监护", "体检", "健康检查", "体检报告"), "C11"),
        (("采光", "通风", "照明", "照度", "建筑卫生"), "C12"),
        (("防护措施", "防护设施", "防毒", "隔声", "除尘", "排风", "防护参数"), "C13"),
        (("个人防护", "防护用品", "ppe", "口罩", "耳塞", "防护装备", "更换周期"), "C14"),
        (("应急救援", "应急预案", "急救", "应急器材", "演练", "喷淋", "洗眼"), "C15"),
        (("职业卫生管理", "管理机构", "防治经费", "保健制度", "职业卫生投入"), "C16"),
    ]
    for keywords, cat in rules:
        for kw in keywords:
            if kw in name:
                return cat

    # ---- 化学品 SDS 识别 → C8 原辅材料 ----
    # SDS 特征: 文件名含厂商名(斑芝/伊士曼/陶氏/巴斯夫/万华... 这些是化学试剂厂商) 或含 SDS/MSDS/安全技术说明书
    ext = Path(filename).suffix.lower()
    _SDS_VENDORS = ("斑芝", "伊士曼", "陶氏", "巴斯夫", "万华", "长兴", "海逸", "信涛", "康塑德",
                    "赢创", "瓦克", "阿科玛", "柏斯托", "索尔维", "华昌", "巨川", "海泰", "泰柯",
                    "昆化", "联成", "江宁", "华峰", "濮阳", "东就", "鑫洋", "上海国药", "洛社",
                    "海睿", "翰兴", "韬元", "林赛", "维科", "毕克", "费舍尔", "汉森", "国药",
                    "铜陵金泰", "天赐", "新特", "武汉有机", "金海威", "联成", "山东华夏", "盈飞",
                    "繁中", "华科", "长龙", "星辰", "赢创", "迈图", "德固赛", "帝斯曼", "科思创")
    _SDS_MAT = ("sds", "msds", "安全技术说明书", "丙二醇", "多聚甲醛", "甲基", "苯酐", "苯乙烯",
                "顺酐", "乙二醇", "树脂", "固化剂", "消泡剂", "分散剂", "阻燃剂", "流平剂",
                "光引发剂", "促进剂", "稀释剂", "助剂", "增塑剂", "溶剂", "催干剂", "单体",
                "醇", "酸", "苯", "酯", "酮", "胺", "酚", "液氮", "柴油", "天然气")
    if ext in (".pdf", ".doc", ".docx"):
        if any(m in name for m in ("sds", "msds", "安全技术说明书")):
            return "C8"
        # 厂商名命中即视为化学品SDS (这些厂商名几乎只出现在化学品MSDS文件名)
        if any(v in name for v in _SDS_VENDORS):
            return "C8"

    # 依据扩展名 + 内容兜底: 表格文件按表头猜
    if ext in (".csv", ".xlsx", ".xls"):
        # 尝试从内容表头判断
        try:
            head = content[:400].decode("utf-8", errors="ignore").lower()
        except Exception:
            head = ""
        if any(k in head for k in ("岗位", "定员", "人数")):
            return "C10"
        if any(k in head for k in ("设备", "型号", "数量")):
            return "C6"
        if any(k in head for k in ("原辅", "物料", "成分", "用量")):
            return "C8"
        if any(k in head for k in ("产品", "产量", "产能")):
            return "C9"
        return "C6"  # 默认表格按设备清单
    return "unknown"


def sort_uploads(files: list[dict]) -> dict[str, list[dict]]:
    """一次批量文件 → 按类别分桶 {cat_key: [文件...]}, unknown 进待定区"""
    buckets: dict[str, list[dict]] = {c["key"]: [] for c in CATEGORIES}
    buckets["uncat"] = []
    for f in files:
        cat = classify_file(f.get("name", ""), f.get("content", b""))
        if cat in buckets:
            buckets[cat].append(f)
        else:
            buckets["uncat"].append(f)
    return buckets


def project_dir(pid: str, create: bool = False) -> Path:
    d = MATERIAL_ROOT / pid
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(pid: str, cat_key: str, filename: str, content: bytes) -> dict:
    """保存上传文件 → 返回 {path, size, cat}"""
    cat = next((c for c in CATEGORIES if c["key"] == cat_key), None)
    if not cat:
        raise ValueError(f"未知类别: {cat_key}")
    safe = Path(filename).name
    suffix = Path(safe).suffix.lower()
    if suffix not in cat["ext"]:
        raise ValueError(f"不支持的文件类型: {suffix or '无扩展名'}")
    safe = "".join(ch for ch in safe if ch not in "/\\:*?\"<>|").strip()
    if not safe or safe in {".", ".."}:
        safe = f"upload_{uuid.uuid4().hex[:8]}{suffix}"

    cat_dir = project_dir(pid, create=True) / f"{cat_key}_{cat['name']}"
    cat_dir.mkdir(exist_ok=True)
    dst = cat_dir / safe
    if dst.exists():
        path = Path(safe)
        safe = f"{path.stem}_{uuid.uuid4().hex[:8]}{path.suffix}"
        dst = cat_dir / safe
    dst.write_bytes(content)
    return {"path": str(dst), "size": len(content), "category": cat_key, "name": safe}


def list_materials(pid: str) -> list[dict]:
    """项目已上传材料 (按类别)"""
    d = project_dir(pid)
    out = []
    if not d.exists():
        return out
    for f in sorted(d.rglob("*")):
        if f.is_file():
            cat = f.parent.name.split("_", 1)[0]
            out.append({"name": f.name, "path": str(f), "size": f.stat().st_size,
                        "category": cat, "kind": f.suffix.lstrip(".").upper()})
    return out


def copy_seed_materials(pid: str) -> int:
    """首次建项目: 复制模拟材料(长兴提取)到项目目录 (兼容旧A2x映射到新类别)"""
    seed = MATERIAL_ROOT
    n = 0
    for f in list(seed.glob("A*.csv")) + list(seed.glob("A*.json")) + list(seed.glob("A*.txt")):
        old_cat = f.name.split("_", 1)[0]
        new_cat = _OLD_A2X_MAP.get(old_cat)
        if new_cat:
            cat = next((c for c in CATEGORIES if c["key"] == new_cat), None)
            if cat:
                cat_dir = project_dir(pid, create=True) / f"{new_cat}_{cat['name']}"
                cat_dir.mkdir(exist_ok=True)
                shutil.copy2(f, cat_dir / f.name)
                n += 1
    return n
