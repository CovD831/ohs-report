"""浦发热电 材料提取 — 生成 data/materials_pf/ (模拟企业材料)

验证目标: 通用prompt体系能否适应不同行业(垃圾焚烧发电)
  行业码: D44 (电力、热力生产和供应业) — 垃圾焚烧发电
  输入源(报告表): 设备(22) / 工序-岗位-危害(3) / 检测(26化学+34噪声)
                / 定员(23) / 类比(21) / 关键控制(5)
"""
import csv
import json
from pathlib import Path

REPORT = "/Users/abaaba/Desktop/law/职业病危害预评价报告/预评新-  浦发热电（备案稿）_extracted.json"
OUT = Path(__file__).resolve().parent.parent / "data" / "materials_pf"


def extract():
    d = json.load(open(REPORT))
    OUT.mkdir(parents=True, exist_ok=True)

    def rows(i):
        return d["tables"][i].get("rows", [])

    made = {}
    # 设备 (表22, 325行)
    with open(OUT / "A2c_设备清单.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["序号", "设备名称", "规格", "数量"])
        for r in rows(22)[1:]:
            if r and len(r) >= 2:
                w.writerow([r[0], r[1], r[2] if len(r) > 2 else "", r[3] if len(r) > 3 else ""])
    made["A2c_设备清单.csv"] = f"{len(rows(22))}行"

    # 工序-岗位-危害 (表3) → 工艺文本+岗位
    proc_lines = []
    post_lines = []
    for r in rows(3)[1:]:
        if r and r[0]:
            proc_lines.append(f"- 车间 {str(r[0]).strip()[:12]} | 工序 {str(r[1]).strip()[:12]} "
                              f"| 密闭 {str(r[3]) if len(r) > 3 else ''} | 危害 {str(r[6])[:40] if len(r) > 6 else ''}")
            post_lines.append(f"{str(r[0]).strip()[:12]},{str(r[2]).strip()[:12]}")
    with open(OUT / "A2b_生产工艺说明.txt", "w", encoding="utf-8") as f:
        f.write("# 生产工艺说明（企业材料）\n# 生产单元-工序-岗位-危害（表3）\n" + "\n".join(proc_lines))
    made["A2b_生产工艺说明.txt"] = f"{len(proc_lines)}行"

    # 化学检测 (表26-32 分解检测: 物质在表头, 数据行)
    det_rows = []
    for ti in range(26, 33):
        for r in rows(ti)[1:]:
            if r and len(r) >= 4 and not str(r[1]).strip().isdigit():
                det_rows.append([str(r[1]).strip()[:16], str(r[4])[:8] if len(r) > 4 else ""])
    with open(OUT / "A2e_类比项目检测数据.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["危害因素", "CTWA"])
        for k, v in det_rows[:40]:
            w.writerow([k, v])
    made["A2e_类比项目检测数据.csv"] = f"{len(det_rows)}行"

    # 定员 (表23)
    with open(OUT / "A2d_劳动定员表.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["部门", "岗位", "人数"])
        for r in rows(23)[1:]:
            if r and len(r) >= 3:
                w.writerow([r[0], r[1], r[2]])
    made["A2d_劳动定员表.csv"] = f"{len(rows(23))}行"

    # 关键词: 表26 表头 (物质名)
    t26 = rows(26)[0] if rows(26) else []
    print("表26 表头:", [str(x)[:12] for x in t26])
    return made


if __name__ == "__main__":
    print("=== 浦发材料提取 ===\n")
    for name, info in extract().items():
        print(f"  📄 {name:28s} {info}")
