"""标准库全量核验 — 评价依据26项 + 废止/待实施清单

任务:
  1. 核验 standard_ref (26项评价依据) → 现行版解析 (standard_db)
  2. 列出废止(20)+待实施(3) → 报告引用需避免
  3. 报告引用规则: 实施中/现行/待实施(注明日期) 可用; 废止不可用
输出: 核验报告 (每条: code/名称/状态/现行版/结论)
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.standard_db_loader import resolve_current  # noqa: E402

REF_196 = [
    ("GB 5083", "生产设备安全卫生设计总则"), ("GB/T 12801", "生产过程安全卫生要求总则"),
    ("GB/T 16758", "排风罩的分类及技术条件"), ("GB/T 18664", "呼吸防护用品的选择使用与维护"),
    ("GB/T 23466", "护听器的选择指南"), ("GB/T 38144", "眼面部防护应急喷淋和洗眼设备"),
    ("GB 39800", "个体防护装备配备规范"), ("GB 50019", "工业建筑供暖通风与空气调节设计规范"),
    ("GB 50033", "建筑采光设计标准"), ("GB/T 50034", "建筑照明设计标准"),
    ("GB 50073", "洁净厂房设计规范"), ("GB 50087", "工业企业噪声控制设计规范"),
    ("GB 50187", "工业企业总平面设计规范"), ("GBZ 1", "工业企业设计卫生标准"),
    ("GBZ 2.1", "工作场所有害因素职业接触限值 第1部分"), ("GBZ 2.2", "工作场所有害因素职业接触限值 第2部分"),
    ("GBZ 158", "工作场所职业病危害警示标识"), ("GBZ 159", "工作场所空气中有害物质监测的采样规范"),
    ("GBZ/T 194", "工作场所防止职业中毒卫生工程防护措施规范"), ("GBZ/T 196", "建设项目职业病危害预评价标准"),
    ("GBZ/T 197", "建设项目职业病危害控制效果评价标准"), ("GBZ 188", "职业健康监护技术规范"),
    ("GBZ/T 225", "用人单位职业病防治指南"), ("GBZ 230", "职业性接触毒物危害程度分级"),
    ("GBZ/T 229.2", "工作场所职业病危害作业分级 第2部分") ,
    ("GBZ/T 229.3", "工作场所职业病危害作业分级 第3部分"),
]


def verify():
    conn = sqlite3.connect("data/ohs.db")
    print("=" * 72)
    print("一、评价依据 26 项 → 现行版核验")
    print("=" * 72)
    problems = []
    for code, name in REF_196:
        r = resolve_current(conn, code)
        if r:
            state = r.get("state", "")
            eff = r.get("effdate", "")
            flag = "✅" if state in ("现行", "实施中") else ("⚠️" if state == "待实施" else "❌")
            if state == "废止":
                problems.append(f"{code}: 现行版为 {r['code']} 但状态={state}!")
            print(f"  {flag} {code:14s} → {r['code']:18s} [{state}] {r['name'][:26]}")
        else:
            print(f"  ❌ {code:14s} → 无记录 (standard_db 缺失)")
            problems.append(f"{code}: 无记录")
    print(f"\n问题: {len(problems)} 项")
    for p in problems:
        print(f"  ⚠️ {p}")

    print("\n" + "=" * 72)
    print("二、废止/待实施标准 (报告引用需避免)")
    print("=" * 72)
    for r in conn.execute("SELECT code, name, state, effdate FROM standard_db WHERE state IN ('废止','待实施') ORDER BY state, code"):
        print(f"  [{r[2]}] {r[0]:22s} {r[1][:36]} (实施{ r[3] or '—'})")
    conn.close()


if __name__ == "__main__":
    verify()
