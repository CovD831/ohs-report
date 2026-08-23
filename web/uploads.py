"""材料上传存储 — 新建项目向导: 附录A 9类材料, 每类独立上传窗口

目录: data/materials/<project_id>/<category序号>_<类别名>/<原文件名>
类别 (GBZ/T 196 附录A):
  A1 立项文件(项目建议书/可研报告/备案证明)
  A2a 原辅材料清单  A2b 生产工艺说明  A2c 设备清单
  A2d 劳动定员表    A2e 类比项目检测数据
  A2f 防护措施      A2g 职业健康检查资料
  A2h 设计图纸(区域位置/总平面/竖向)
  A3 法规标准(可选)
上传后: 自动导入 (设备/检测/工艺文本) 已有解析器
"""
import shutil
import uuid
from pathlib import Path

MATERIAL_ROOT = Path(__file__).resolve().parent.parent / "data" / "materials"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# 类别定义 (窗口顺序)
CATEGORIES = [
    {"key": "A1", "name": "立项文件", "desc": "项目建议书/可行性研究报告/项目备案证明", "ext": [".doc", ".docx", ".pdf", ".txt", ".json"]},
    {"key": "A2a", "name": "原辅材料清单", "desc": "原料/辅料/产品/副产品/中间品", "ext": [".xlsx", ".csv", ".doc", ".docx", ".pdf"]},
    {"key": "A2b", "name": "生产工艺说明", "desc": "工艺流程描述/流程图", "ext": [".doc", ".docx", ".pdf", ".txt"]},
    {"key": "A2c", "name": "设备清单", "desc": "设备名称/规格/数量/内部物料", "ext": [".xlsx", ".csv", ".doc", ".docx"]},
    {"key": "A2d", "name": "劳动定员表", "desc": "车间/工种/人数/工作内容", "ext": [".xlsx", ".csv", ".doc", ".docx"]},
    {"key": "A2e", "name": "类比项目检测数据", "desc": "类比项目检测报告(化学/物理)", "ext": [".xlsx", ".csv", ".doc", ".docx"]},
    {"key": "A2f", "name": "防护措施", "desc": "拟采取的防护设施/用品清单", "ext": [".doc", ".docx", ".pdf", ".txt"]},
    {"key": "A2g", "name": "职业健康检查资料", "desc": "改扩建项目: 现有劳动者健康检查", "ext": [".doc", ".docx", ".pdf", ".xlsx"]},
    {"key": "A2h", "name": "设计图纸", "desc": "区域位置图/总平面布置图/竖向布置图", "ext": [".dwg", ".pdf", ".png", ".jpg"]},
    {"key": "A3", "name": "法规标准(可选)", "desc": "适用的地方法规/行业标准", "ext": [".pdf", ".txt"]},
]


def classify_file(filename: str, content: bytes = b"") -> str:
    """按文件名关键词推断类别, 返回类别 key 或 'unknown' (进待定区)"""
    name = filename.lower()
    # 精确|包含关键词 → 类别 (启发式, 多个命中按优先级)
    rules = [
        (("立项", "项目建议书", "可研", "可行性", "备案", "hse-"), "A1"),
        (("原辅材料", "原辅料", "原料", "物料清单", "化学", "成分"), "A2a"),
        (("工艺", "流程", "生产方式", "作业"), "A2b"),
        (("设备", "机器", "装备", "设施清单"), "A2c"),
        (("定员", "劳动", "人员", "岗位", "编制"), "A2d"),
        (("检测", "监(测)?", "分析报告", "类比", "ctwa", "twa"), "A2e"),
        (("防护", "pp", "措施", "劳动保护", "应急"), "A2f"),
        (("健康检查", "体检", "职业健康", "health"), "A2g"),
        (("图纸", "平面", "总平", "布置", "区域位置", "竖向", "设计图", "dwg"), "A2h"),
        (("法规", "标准", "规范", "gb", "gbz"), "A3"),
    ]
    for keywords, cat in rules:
        for kw in keywords:
            if kw in name:
                return cat
    # 依据扩展名兜底: 表格多半是设备/原料/检测
    ext = Path(filename).suffix.lower()
    if ext in (".csv", ".xlsx", ".xls"):
        return "A2x"  # 不确定的表格 → 待定区
    return "unknown"


def sort_uploads(files: list[dict]) -> dict[str, list[dict]]:
    """一次批量文件 → 按类别分桶 {cat_key: [文件...]}, unknown/A2x 进待定区"""
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
    """首次建项目: 复制模拟材料(长兴提取)到项目目录"""
    # 用现有 data/materials/* 作为 seed
    seed = MATERIAL_ROOT
    n = 0
    for f in list(seed.glob("A*.csv")) + list(seed.glob("A*.json")) + list(seed.glob("A*.txt")):
        cat = f.name.split("_", 1)[0]
        # 按类别分配
        cat_map = {"A1": "A1", "A2a": "A2a", "A2b": "A2b", "A2c": "A2c",
                   "A2d": "A2d", "A2e": "A2e", "A2f": "A2f", "A2g": "A2g", "A2h": "A2h"}
        if cat in cat_map:
            cat_dir = project_dir(pid, create=True) / f"{cat}_{next(c['name'] for c in CATEGORIES if c['key'] == cat)}"
            cat_dir.mkdir(exist_ok=True)
            shutil.copy2(f, cat_dir / f.name)
            n += 1
    return n
