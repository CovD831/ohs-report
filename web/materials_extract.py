"""附录A材料反向提取 — 从已有报告提取'企业提供的原始材料文件'

依据: GBZ/T 196—2025 附录A (A.1 立项文件 / A.2 技术资料)
  从长兴报告 extracted JSON 反向提取 → data/materials/ 各类文件
  模拟企业提供材料 (报告表格≈企业材料, 用于管线测试)

材料文件清单 (文件 → 报告表):
  A1_立项_项目概况.json        ← 表0/8/12 (名称/产能/指标)
  A2a_原辅材料清单.csv        ← 表18 (名称/浓度/相态) + 表13
  A2b_生产工艺说明.txt        ← 表2/29/36 (车间工序+密闭) → 工艺段落
  A2c_设备清单.csv            ← 表17 (315台: 名称/规格/物料)
  A2d_劳动定员表.csv          ← 表21 (车间/工种/人数)
  A2e_类比项目检测数据.csv    ← 表25 (岗位/地点/CTWA/限值) 模拟类比检测
  A2f_防护措施.txt            ← 表5/24 (岗位/防护用品)
  A2g_职业健康检查.txt        ← 表7 (危害因素/体检类别)
  图纸/现场检测 等            ← (无, 标注'待企业提供')
"""
import csv
import json
from pathlib import Path

REPORT = Path("/Users/abaaba/Desktop/law/职业病危害预评价报告/长兴--预评（备案稿7-31）_extracted.json")
OUT = Path(__file__).resolve().parent.parent / "data" / "materials"
OUT_DIR = OUT


def _rows(d, i):
    return d["tables"][i].get("rows", [])


def extract_all() -> dict:
    d = json.load(open(REPORT))
    OUT.mkdir(parents=True, exist_ok=True)
    made = {}

    # A1 立项/项目概况 (表8: 项目基本情况)
    rows8 = _rows(d, 8)
    with open(OUT / "A1_立项_项目概况.json", "w", encoding="utf-8") as f:
        json.dump({"name": "A.1 立项文件-项目概况", "source": "企业可行性研究报告/项目建议书",
                   "items": [{"项目": str(r[0]), "情况": str(r[1])} for r in rows8[1:] if r and len(r) > 1]},
                  f, ensure_ascii=False, indent=1)
    made["A1_立项_项目概况.json"] = f"{len(rows8)} 行 (表8)"

    # A2a 原辅材料 (表18: 名称/浓度/相态)
    rows18 = _rows(d, 18)
    with open(OUT / "A2a_原辅材料清单.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["序号", "名称", "浓度/纯度", "相态", "备注"])
        for r in rows18[1:]:
            if r and len(r) >= 3:
                w.writerow([r[0], r[1], r[2] if len(r) > 2 else "", r[3] if len(r) > 3 else "", ""])
    made["A2a_原辅材料清单.csv"] = f"{len(rows18)} 行 (表18)"

    # A2b 生产工艺 (表2/29/36 车间-工序-密闭)
    rows36 = _rows(d, 36)  # 关键控制措施表 (工序/危害/措施)
    with open(OUT / "A2b_生产工艺说明.txt", "w", encoding="utf-8") as f:
        f.write("# 生产工艺说明（企业提供）\n")
        f.write("来源: 可研报告/工艺方案\n\n# 各车间生产工序及密闭情况（表29/36）\n")
        for r in rows36[1:]:
            if r and r[0]:
                f.write(f"- 车间 {str(r[0]).strip()[:12]} | 工序 {str(r[1]).strip()[:12]} | "
                        f"密闭 {str(r[2])[:10]} | 危害 {str(r[3])[:20]}\n")
    made["A2b_生产工艺说明.txt"] = f"{len(rows36)} 行 (表36)"

    # A2c 设备清单 (表17: 315台)
    rows17 = _rows(d, 17)
    with open(OUT / "A2c_设备清单.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["序号", "设备名称", "规格型号", "材质", "数量", "内部物料"])
        for r in rows17[1:]:
            if r and len(r) >= 2:
                w.writerow([r[0], r[1], r[2] if len(r) > 2 else "",
                            r[3] if len(r) > 3 else "", r[4] if len(r) > 4 else "",
                            r[5] if len(r) > 5 else ""])
    made["A2c_设备清单.csv"] = f"{len(rows17)} 行 (表17)"

    # A2d 劳动定员 (表21: 车间/工种/人数)
    rows21 = _rows(d, 21)
    with open(OUT / "A2d_劳动定员表.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["车间", "工种", "人数", "工作内容"])
        for r in rows21[1:]:
            if r and r[0]:
                w.writerow([r[0], r[1], r[2], r[3] if len(r) > 3 else ""])
    made["A2d_劳动定员表.csv"] = f"{len(rows21)} 行 (表21)"

    # A2e 类比检测 (表25: 化学 CTWA 分表) + 表28 (因子清单) — 模拟类比检测报告
    rows25 = _rows(d, 25)
    rows28 = _rows(d, 28)  # 因子清单: 序号/因子/岗位数/合格数
    PHYS = ("工频", "噪声", "高温", "紫外", "辐射", "振动", "电场", "磁场", "WBGT")
    factors = [str(r[1]).strip() for r in rows28[1:] if r and len(r) > 1 and r[1]
               and not any(p in str(r[1]) for p in PHYS)]
    # 物理因素单独记录 (检测数据只有化学, 物理从识别引擎出)
    phys_factors = [str(r[1]).strip() for r in rows28[1:] if r and len(r) > 1 and r[1]
                    and any(p in str(r[1]) for p in PHYS)]
    with open(OUT / "A2e_类比项目检测数据.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["序号", "危害因素", "岗位", "CTWA(mg/m3)", "PC-TWA", "判定"])
        # 用表28 的因子+岗位数, CTWA 从表25 匹配 (岗位含因子名)
        for i, fac in enumerate(factors, 1):
            ctwa = ""
            for r in rows25[1:]:
                if r and len(r) > 3 and fac in str(r[0]):
                    ctwa = str(r[3]).replace("＜", "<")
                    break
            if not ctwa:
                # 表28因子可能不在表25(表25只列部分), 默认<1(未检出)
                ctwa = "<1"
            w.writerow([i, fac, "各岗位", ctwa, "", "合格"])
    made["A2e_类比项目检测数据.csv"] = f"{len(factors)} 化学因子 (表28+表25, 物理{len(phys_factors)}项跳过)"

    # A2f 防护措施 (表5: 岗位/防护用品)
    rows5 = _rows(d, 5)
    with open(OUT / "A2f_防护措施.txt", "w", encoding="utf-8") as f:
        f.write("# 拟配备的个人防护用品（企业材料）\n")
        for r in rows5[1:]:
            if r and r[0]:
                f.write(f"- {r[0]} | {r[1]}\n")
    made["A2f_防护措施.txt"] = f"{len(rows5)} 行 (表5)"

    # A2g 职业健康检查 (表7)
    rows7 = _rows(d, 7)
    with open(OUT / "A2g_职业健康检查.txt", "w", encoding="utf-8") as f:
        f.write("# 拟开展的职业健康检查（企业材料, 改扩建项目）\n")
        for r in rows7[1:]:
            if r and r[0]:
                f.write(f"- {r[0]} | {r[1]} | {r[2]}\n")
    made["A2g_职业健康检查.txt"] = f"{len(rows7)} 行 (表7)"

    # 图纸/现场检测 (标注待提供)
    with open(OUT / "A2h_设计图纸_说明.txt", "w", encoding="utf-8") as f:
        f.write("# 待企业提供（本测试无）\n- 区域位置图\n- 总平面布置图\n- 竖向布置图\n")
    made["A2h_设计图纸_说明.txt"] = "占位"

    return made


if __name__ == "__main__":
    print("=== 附录A 材料反向提取 (长兴报告 → 模拟企业材料) ===\n")
    for name, info in extract_all().items():
        print(f"  📄 {name:30s} {info}")
    print(f"\n共 {len(list(OUT.glob('*')))} 个材料文件 → data/materials/")
