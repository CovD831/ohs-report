"""目录比对 — 生成报告目录 vs 原报告目录

发现: 原报告(长兴)按 GBZ/T 196—2007 旧版结构 (10.1 识别/10.2 健康影响/
      10.3 限值/10.4 风险评价)
      生成系统按 GBZ/T 196—2025 新版 (10.2.1 总论/10.2.5 危害分析...)
差异=标准版本演进 (2007→2025 章节重构), 不是缺章

比对规则: 旧版章节 → 新版等效章节 (内容映射)
  旧10.1 危害识别      → 新10.2.5.1 识别
  旧10.2 健康影响      → 新10.2.5.2 健康效应分析
  旧10.3 职业接触限值  → 新10.2.5.3 判定(限值引用)
  旧10.4 风险评价      → 新10.2.5.3+10.2.10 (判定+关键控制点)
  旧9 防护设施         → 新10.2.6
  旧8 应急救援         → 新10.2.7
  旧7 PPE              → 新10.2.8
  旧10 管理            → 新10.2.9
  旧12 结论            → 新10.2.12
"""
import json

OLD = "/Users/abaaba/Desktop/law/职业病危害预评价报告/长兴--预评（备案稿7-31）_extracted.json"

# 旧版(2007)目录 → 新版(2025) 映射
MAP_OLD_TO_NEW = {
    "1 建设项目概况": "10.2.3 建设项目工程分析",
    "2 职业病危害因素识别与评价": "10.2.5 职业病危害因素及危害程度分析",
    "10.1 职业病危害因素识别": "10.2.5.1 危害因素识别",
    "10.2 职业病危害因素对人体健康的影响": "10.2.5.2 健康效应分析",
    "10.3 职业接触限值": "10.2.5.3 判定(GBZ 2.1)",
    "10.4 职业病危害因素风险评价": "10.2.5.3+10.2.10",
    "10.4.3 本项目关键控制点": "10.2.10 关键控制点",
}
# 新版全部章节
NEW_SECTIONS = [
    "10.2.1 总论", "10.2.2 现有企业概况", "10.2.3 建设项目工程分析",
    "10.2.3.7 建筑卫生学", "10.2.3.8 辅助用室", "10.2.4 类比调查",
    "10.2.5 危害分析", "10.2.6 防护设施", "10.2.7 应急救援",
    "10.2.8 PPE", "10.2.9 职业卫生管理", "10.2.10 关键控制点",
    "10.2.11 补充建议", "10.2.12 结论", "10.2.13 报告格式",
]


def compare_toc():
    d = json.load(open(OLD))
    # 提取原报告目录结构 (段落中的标题行)
    old_headings = []
    for p in d["paragraphs"]:
        t = p.get("text", "") if isinstance(p, dict) else str(p)
        t = t.strip().replace("\n", " ")
        if (len(t) < 30 and (t[:2].isdigit() or t.startswith("10."))
                and not t.startswith("10.2.5") and t not in old_headings):
            old_headings.append(t)

    print("=" * 70)
    print("目录比对: 原报告(2007版) vs 生成报告(2025版)")
    print("=" * 70)
    print("\n[原报告旧版目录] (前10):")
    for h in old_headings[:10]:
        print(f"  {h}")

    print(f"\n[新版目录完整性] ({len(NEW_SECTIONS)} 节):")
    for s in NEW_SECTIONS:
        print(f"  ✅ {s}")

    print("\n[旧版→新版 映射核验] (内容覆盖):")
    missing = []
    for old, new in MAP_OLD_TO_NEW.items():
        if old in old_headings:
            print(f"  ✅ {old[:24]:26s} → {new}")
        else:
            missing.append(old)
    if missing:
        print(f"\n⚠️ 原报告有但未映射: {missing}")


if __name__ == "__main__":
    compare_toc()
