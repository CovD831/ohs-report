"""材料导入器 — 模拟企业上传原始材料 → 提取 → 项目数据

流程 (对照真实业务):
  上传材料文件 (data/materials/*) → 解析器 (csv/json/txt) →
  结构化项目数据 {industry, equipment[], detections[], processes[], process_text}
  → 评估管线 (识别/判定/分级/报告)

材料 → 项目数据映射:
  A2c_设备清单.csv       → equipment[] (315台)
  A2e_类比项目检测数据.csv → detections[] (CTWA/限值→判定)
  A2b_生产工艺说明.txt    → process_text (+工艺段落)
  A2a_原辅材料清单.csv    → process_text补充 (物料名)
  A1_立项_项目概况.json   → 项目概况段落
"""
import csv
import json
from pathlib import Path

MATERIALS = Path(__file__).resolve().parent.parent / "data" / "materials"


def load_equipment(path: Path) -> list[str]:
    """设备清单.csv → 设备名列表 (含内部物料)"""
    eqs = []
    if not path.exists():
        return eqs
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("设备名称") or "").strip()
            mat = (row.get("内部物料") or "").strip()
            if name:
                eqs.append(f"{name}|{mat}" if mat else name)
    return eqs


def load_detections(path: Path) -> list[dict]:
    """类比检测.csv → 检测条目 (危害因素+CTWA, <1→0.5 代表未检出)"""
    dets = []
    if not path.exists():
        return dets
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ctwa_raw = (row.get("CTWA(mg/m3)") or "").strip()
            if not ctwa_raw:
                ctwa_f = None
            elif "<" in ctwa_raw or "＜" in ctwa_raw:
                ctwa_f = 0.5  # 未检出近似
            else:
                try:
                    ctwa_f = float(ctwa_raw)
                except ValueError:
                    ctwa_f = None
            factor = (row.get("危害因素") or "").strip()
            dets.append({"factor": factor, "ctwa": ctwa_f})
    return dets


def load_process_text(path: Path) -> str:
    """工艺说明.txt → 段落文本"""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_material_names(path: Path) -> list[str]:
    """原辅材料.csv → 物料名 (工艺文本补充)"""
    names = []
    if not path.exists():
        return names
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            n = (row.get("名称") or "").strip()
            if n:
                names.append(n)
    return names


def import_materials() -> dict:
    """从材料目录导入 → 项目数据"""
    eq = load_equipment(MATERIALS / "A2c_设备清单.csv")
    dets = load_detections(MATERIALS / "A2e_类比项目检测数据.csv")
    proc = load_process_text(MATERIALS / "A2b_生产工艺说明.txt")
    mats = load_material_names(MATERIALS / "A2a_原辅材料清单.csv")
    return {
        "equipment": eq,
        "detections": dets,
        "process_text": proc + "\n\n# 原辅材料\n" + "、".join(mats[:30]),
        "material_count": len(mats),
    }


def show_sample(data: dict) -> None:
    print(f"设备: {len(data['equipment'])} 台, 示例: {data['equipment'][:3]}")
    print(f"检测: {len(data['detections'])} 条, 示例: {data['detections'][:3]}")
    print(f"材料: {data['material_count']} 个, 工艺文本 {len(data['process_text'])} 字")


if __name__ == "__main__":
    print("=== 材料导入 (模拟企业上传) ===\n")
    data = import_materials()
    show_sample(data)
