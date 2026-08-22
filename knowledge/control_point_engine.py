"""职业病危害关键控制点规则引擎 (GBZ/T 196—2025 10.2.10) — 工序级

原理: 关键控制点 = 主要危害 × 接触人数 × 严重程度 (原文三要素)
  结合报告表29(工序→物料→产生环节→危害) + 表36(工序→危害→措施) 结构
  工序级识别: 每个 工序×危害 条目 = 控制点候选

评分 (每控制点 0-100, 加权):
  S1 危害严重程度 (40%): 超标(判定) / 作业分级等级 / 目录危害程度(剧毒高毒)
  S2 接触人数 (30%): 岗位人数 (≥10=100, 3-9=60, 1-2=30)
  S3 防护措施强度 (30%): 自动化密闭化机械化=20(好) | 半密闭=50 | 敞开+人工=90

判定: ≥70 关键控制点★★★ | 50-69 重点★★ | 30-49 关注★ | <30 一般
输出: 按评分排序的控制点清单 (优先控制)

用法: python3 -m knowledge.control_point_engine
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 防护措施强度评分 (GBZ/T 196 10.2.6 防护设施类型)
MEASURE_SCORE = {
    "自动化": 20, "密闭化": 20, "机械化": 40, "局部通风": 50,
    "全面通风": 60, "个人防护": 80, "敞开": 90, "人工": 100,
}
# 危害特性: 剧毒/高毒/致癌/粉尘/噪声/高温
HAZARD_KW = {
    "剧毒": 100, "高毒": 90, "致癌": 85, "致癌物": 85,
    "苯": 75, "甲醛": 75, "石棉": 80, "矽尘": 80, "铬": 70, "铍": 70,
    "粉尘": 60, "噪声": 55, "高温": 50, "振动": 50,
}


def control_points(units: list[dict], judgements: list[dict] | None = None,
                   posts: list[dict] | None = None) -> list[dict]:
    """工序级关键控制点
    units: [{unit(生产车间), process(工序), factors(危害,;分隔), sealed(密闭/敞开),
             contact(接触方式), measures(关键控制措施)}]
    judgements: [{factor, pass, level}]
    posts: [{unit, job, count}] 岗位人数"""
    judge_map = {j.get("factor"): j for j in (judgements or [])}
    post_count = {}
    for p in posts or []:
        post_count[p.get("unit", "")] = post_count.get(p.get("unit", ""), 0) + int(p.get("count", 0) or 0)

    out = []
    for u in units:
        unit = u.get("unit", "")
        process = u.get("process", "")
        factors = [f.strip() for f in (u.get("factors") or "").split(";") if f.strip()]
        sealed = u.get("sealed", "密闭")
        measures = u.get("measures", "")
        n_post = post_count.get(unit, 0)
        for f in factors:
            # S1 危害严重程度 (0-100)
            s1 = 40.0
            j = judge_map.get(f)
            if j and j.get("pass") is False:
                s1 = 100
            elif j and isinstance(j.get("level"), dict):
                s1 = {"0": 15, "Ⅰ": 35, "Ⅱ": 60, "Ⅲ": 85, "Ⅳ": 100}.get(j["level"]["level"], 40)
            for kw, sc in HAZARD_KW.items():
                if kw in f or (kw in measures):
                    s1 = max(s1, sc - 10)
            # S2 接触人数
            s2 = 100 if n_post >= 10 else (60 if n_post >= 3 else (30 if n_post >= 1 else 20))
            # S3 防护措施 (密闭程度+措施里有无自动化)
            s3 = 50.0
            if "密闭" in sealed and "自动" in measures:
                s3 = 20
            elif "密闭" in sealed:
                s3 = 35
            if "敞开" in sealed or "人工" in measures:
                s3 = 90
            total = 0.4 * s1 + 0.3 * s2 + 0.3 * s3
            level = ("关键控制点" if total >= 70 else "重点" if total >= 50
                     else "关注" if total >= 30 else "一般")
            stars = "★★★" if total >= 70 else "★★" if total >= 50 else "★" if total >= 30 else ""
            out.append({
                "unit": unit, "process": process, "factor": f,
                "score": round(total, 1),
                "scores": {"S1危害": s1, "S2人数": s2, "S3防护": s3},
                "contact": n_post, "sealed": sealed,
                "measures": measures, "level": level, "stars": stars,
            })
    out.sort(key=lambda x: -x["score"])
    return out


if __name__ == "__main__":
    # 演示: 长兴报告表29/36 工序级数据 → 关键控制点
    import json
    import re

    def norm_unit(s: str) -> str:
        """车间名归一化: 全角括号→半角, 去'扩建'"""
        return s.replace("（", "(").replace("）", ")").replace("扩建", "").strip()

    d = json.load(open("/Users/abaaba/Desktop/law/职业病危害预评价报告/长兴--预评（备案稿7-31）_extracted.json"))
    # 表29: 车间/工艺/工序/密闭/物料/产生环节/危害因素
    units = []
    for r in d["tables"][29]["rows"][1:]:
        if r and r[0] and r[1]:
            fx = str(r[6]).strip() if len(r) > 6 and r[6] else ""
            factors = fx.replace("、", ";").replace(",", ";")
            units.append({
                "unit": norm_unit(str(r[0])), "process": str(r[2]) if len(r) > 2 else "",
                "sealed": str(r[3]) if len(r) > 3 else "密闭",
                "factors": factors,
                "measures": "自动化、密闭化、机械化" if "密闭" in str(r[3]) else "",
            })
    # 表21 岗位人数 (归一化 unit)
    posts = []
    for r in d["tables"][21]["rows"][1:]:
        if r and r[0]:
            posts.append({"unit": norm_unit(str(r[0])), "count": int(str(r[2]).strip() or 0) if str(r[2]).strip().isdigit() else 1})
    cps = control_points(units[:50], [], posts)
    print(f"=== 长兴项目关键控制点 (工序级, 共{len(cps)}个) ===")
    for cp in cps[:10]:
        print(f"  {cp['stars']} 综合{cp['score']:5.1f} [{cp['unit']}/{cp['process']}] {cp['factor'][:22]}"
              f" (S1{cp['scores']['S1危害']:.0f}/S2{cp['scores']['S2人数']:.0f}/S3{cp['scores']['S3防护']:.0f}) 接触{cp['contact']}人")
