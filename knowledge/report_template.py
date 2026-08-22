"""报告模板引擎 — 章节骨架 + 数据插槽 (成文阶段原型)

原理:
  模板 = 标准结构 (GBZ/T 196 10.2.x 各节固定顺序) + 数据插槽 (引擎输出)
  不学习报告文字内容 (只有2篇样本, 过拟合风险), 只提取"表述套路"
  段落层: 模板句 + 引擎数据
  表格层: 引擎输出 → 各章节标准表 (识别/判定/分级/PPE/监护/照度/结论)

2篇报告的作用: ①表格格式参考 ②段落表述套路 ③样本校验(compare_report)
  局限: 长兴(化工)+浦发(电力) 两类项目, 模板需通用(13节全覆盖)

用法: python3 -m knowledge.report_template
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ===== 段落模板 (表述套路来自报告, 内容=引擎数据) =====
PARA_TPL = {
    "10.2.5.1_引子": "本项目可能产生的主要职业病危害因素为{hazards}。",
    "10.2.5.1_来源": "有毒物料、设备密闭不严处跑冒滴漏、蒸汽加热过程中产生的废气、"
                     "设备运行产生噪声等是主要产生环节。",
    "10.2.5.3_判定": "对照GBZ 2.1—2019和GBZ 2.2—2007标准，"
                     "本项目各岗位职业病危害因素预期接触水平判定结果如下：",
    "10.2.12_结论": "该项目属于职业病危害{category}的建设项目。"
                    "存在问题{problems}项；在采取预评价报告提出的职业病防治措施补充建议后，"
                    "职业病防治方面{feasibility}。",
}

# ===== 表格骨架 (列结构, 引擎输出→行) =====
TABLE_TPL = {
    "识别表": ["序号", "职业病危害因素", "产生环节", "接触岗位", "检测方法"],
    "判定表": ["序号", "职业病危害因素", "检测值CTWA", "PC-TWA", "CSTEL", "PC-STEL", "判定"],
    "分级表": ["序号", "职业病危害因素", "G值", "作业级别"],
    "PPE表": ["序号", "危害因素", "防护装备", "标准号"],
    "监护表": ["序号", "危害因素", "检查类别", "检查周期", "必检项目"],
    "照度表": ["序号", "房间/场所", "参考面", "照度标准值(lx)"],
    "结论表": ["序号", "结论要素", "结论"],
}


def build_1025_section(assess_result: dict) -> dict:
    """10.2.5 章节生成 (识别+判定 结构)"""
    hazards = assess_result["hazards"]
    judgements = assess_result["judgements"]
    # 识别段落
    names = "、".join(h["factor"] for h in hazards)
    intro = PARA_TPL["10.2.5.1_引子"].format(hazards=names)
    src = PARA_TPL["10.2.5.1_来源"]
    # 识别表
    rec_rows = []
    for i, h in enumerate(hazards, 1):
        rec_rows.append([i, h["factor"], "; ".join(h["sources"][:2]),
                         "各岗位", h["method"] or "—"])
    # 判定段落+表
    judge_intro = PARA_TPL["10.2.5.3_判定"]
    judge_rows = []
    for i, j in enumerate(judgements, 1):
        judge_rows.append([i, j["factor"],
                           str(j.get("checks", [{}])[0].get("value", "—") if j.get("checks") else "—"),
                           str(j.get("checks", [{}])[0].get("limit", "—") if j.get("checks") else "—"),
                           "—", "—", "合格" if j.get("pass") else "不合格"])
    return {
        "section": "10.2.5",
        "paragraphs": [intro, src, judge_intro],
        "tables": [
            {"name": "识别表", "cols": TABLE_TPL["识别表"], "rows": rec_rows},
            {"name": "判定表", "cols": TABLE_TPL["判定表"], "rows": judge_rows},
        ],
    }


def render_markdown(section: dict) -> str:
    """渲染为 Markdown (演示; Word用python-docx阶段)"""
    out = [f"\n## {section['section']} 职业病危害因素及危害程度分析\n"]
    for i, p in enumerate(section["paragraphs"]):
        out.append(f"{'（一）' if i == 0 else ''}{p}\n" if i == 0 else f"{p}\n")
    for t in section["tables"]:
        out.append(f"\n**{t['name']}**\n")
        out.append("| " + " | ".join(t["cols"]) + " |")
        out.append("|" + "|".join(["---"] * len(t["cols"])) + "|")
        for r in t["rows"]:
            out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def main():
    import json
    from knowledge.project_assess import assess_project
    from knowledge.oel import connect
    conn = connect()
    d = json.load(open("/Users/abaaba/Desktop/law/职业病危害预评价报告/长兴--预评（备案稿7-31）_extracted.json"))
    equip = [str(r[1]).strip() for r in d["tables"][17]["rows"][1:] if len(r) >= 2 and r[1]]
    project = {
        "name": "长兴特殊材料", "industry": "C261",
        "equipment": equip[:15],
        "detections": [{"factor": "甲苯", "ctwa": 30, "cste": 95},
                       {"factor": "环己烷", "ctwa": 0.3, "peak": 4.0}],
    }
    result = assess_project(conn, project)
    conn.close()
    section = build_1025_section(result)
    print(render_markdown(section)[:2200])


if __name__ == "__main__":
    main()
