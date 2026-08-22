"""设备知识库组装验证 — 从设备清单推断危害因素 (识别引擎雏形)

链路: 设备 → 物料 → material_dictionary → OEL 物质 → 危害候选
      ∪ work_unit_rule (工序规则) → 行业危害候选
输出: 项目级危害因素识别结果 (第一轮: 设备清单输入 → 危害因素推断)

用法: python3 -m knowledge.identify_demo [设备清单文件.json]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402


def split_materials(text: str) -> list[str]:
    text = text.replace("\n", " ")
    parts = re.split(r"[、,，/;\s]+", text)
    out = []
    for p in parts:
        p = re.sub(r"^(物料|外部|介质|罐内)[：:]?", "", p).strip()
        p = re.sub(r"^(外半管|内蛇管|外盘管|壳程|管程|槽内|槽体|筒体|主体|内换热管)[：:]?", "", p).strip()
        p = re.sub(r"^[（(].*?[）)]$", "", p).strip()
        if 1 < len(p) <= 15 and not p.isdigit():
            out.append(p)
    return out


def identify(conn, equipment_names: list[str]) -> dict:
    """设备清单 → 危害因素 (按物料映射)"""
    factors = {}
    for eq in equipment_names:
        mats = conn.execute(
            "SELECT DISTINCT material FROM equipment_material WHERE equipment LIKE ?",
            (f"%{eq}%",)).fetchall()
        for (m,) in mats:
            for word in split_materials(m):
                hit = conn.execute(
                    "SELECT oel_factor, cas, fuzzy FROM material_dictionary WHERE material=?",
                    (word,)).fetchone()
                if hit:
                    factors.setdefault(hit[0], set()).add(f"设备[{eq}]")
    return factors


def main():
    conn = connect()
    # Demo: 长兴化工厂的一套核心设备
    demo_eqs = ["酯化釜", "纯化槽", "洗涤塔", "溶剂回收槽", "中和真空槽"]
    print("=== 识别引擎演示: 长兴化工 5 台核心设备 ===")
    result = identify(conn, demo_eqs)
    for factor, srcs in sorted(result.items()):
        print(f"  🔴 {factor}  ← {', '.join(sorted(srcs))}")
    print("\n=== 对照: 浦发热电垃圾焚烧工序规则 ===")
    for r in conn.execute(
            "SELECT unit, process, factors FROM work_unit_rule WHERE process LIKE '%焚烧%'"):
        print(f"  🔴 {r[2]}  ← {r[0]}/{r[1]}")
    conn.close()


if __name__ == "__main__":
    main()
