"""从长兴报告 extracted JSON 提取原始输入 → 喂给评估管线

提取: 设备清单(表17) / 工序(表2/29) / 检测数据(表25/26) / 岗位(表21)
      / 危害因素(表31) / OEL(表33) / 防护用品(表5) — 作为管线输入
比对: 管线生成 10.2 数据 vs 报告实际内容 (覆盖率/差异)
用法: python3 -m knowledge.extract_report_inputs
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.project_assess import assess_project  # noqa: E402
from knowledge.oel import connect  # noqa: E402

REPORT = "/Users/abaaba/Desktop/law/职业病危害预评价报告/长兴--预评（备案稿7-31）_extracted.json"


def load_report():
    return json.load(open(REPORT, encoding="utf-8"))


def extract_inputs(d) -> dict:
    """从报告提取管线原始输入 (设备/工序/检测/岗位)"""
    tables = d["tables"]
    # 表17 设备清单: 序号/设备名称/规格/材质/数量/操作条件/内部物料
    equipment, equipment_mats = [], {}
    for r in tables[17]["rows"][1:]:
        if len(r) >= 7 and r[1]:
            name = str(r[1]).strip()
            equipment.append(name)
            mat = str(r[6]).strip() if r[6] else ""
            equipment_mats[name] = mat
    # 表21 岗位: 车间/工种/人数/作业点.../体力劳动强度
    posts = []
    for r in tables[21]["rows"][1:]:
        if r and r[0]:
            posts.append({"unit": str(r[0]), "job": str(r[1])})
    # 表25/26 检测数据: 岗位/地点/接触时间/CTWA/CSTEL/PC-TWA/PC-STEL/结果
    detections = []
    for tb in (tables[25], tables[26]):
        for r in tb["rows"][1:]:
            if not r or not r[0]:
                continue
            detections.append({
                "post": str(r[0]), "place": str(r[1]) if len(r) > 1 else "",
                "hours": str(r[2]) if len(r) > 2 else "",
                "ctwa": str(r[3]) if len(r) > 3 else "",
                "cste": str(r[4]) if len(r) > 4 else "",
            })
    return {
        "name": "长兴特殊材料(苏州)年产27080吨高性能光固化涂料材料项目",
        "industry": "C261",
        "equipment": equipment,
        "equipment_mats": equipment_mats,
        "posts": posts,
        "detections": detections,
    }


def main():
    d = load_report()
    inputs = extract_inputs(d)
    print(f"提取输入: 设备 {len(inputs['equipment'])} 台, 岗位 {len(inputs['posts'])} 个, "
          f"检测 {len(inputs['detections'])} 条")
    # 跑管线 (用设备清单)
    conn = connect()
    project = {"name": inputs["name"], "industry": inputs["industry"],
               "equipment": inputs["equipment"],
               "processes": inputs["posts"]}
    result = assess_project(conn, project)
    conn.close()
    # 输出管线识别的危害因素
    print(f"\n管线识别危害因素: {len(result['hazards'])} 项")
    for h in result["hazards"]:
        print(f"  {h['factor']} ← {len(h['sources'])} 个来源")
    # 报告实际危害因素 (表31)
    report_factors = set()
    for r in d["tables"][31]["rows"][1:]:
        if r and r[0]:
            report_factors.add(str(r[0]).strip())
    print(f"\n报告危害因素: {len(report_factors)} 项: {sorted(report_factors)}")
    # 覆盖率
    pipe_factors = {h["factor"] for h in result["hazards"]}
    overlap = pipe_factors & report_factors
    print(f"\n管线∩报告: {len(overlap)} 项: {sorted(overlap)}")
    print(f"管线独有: {sorted(pipe_factors - report_factors)}")
    print(f"报告独有: {sorted(report_factors - pipe_factors)}")


if __name__ == "__main__":
    main()
