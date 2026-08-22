"""逐章内容分析 v2 — 生成(section_states) vs 原报告(映射章节)

可靠方法:
  - 生成内容: 直接读 project.data.section_states (LLM正文, 不解析docx)
  - 原报告: 映射的旧版章节 (1概况/10.1识别/2识别评价/3防护/4综合/5建议/6结论)
  - 每章: 字数 / 生成的内容点 / 原报告的分节结构 (看角度差异)
分析根因: 字数差是[详略]还是[角度]?
"""
import json
import re
from pathlib import Path

from web.projects_db import get_project

REPORT_JSON = "/Users/abaaba/Desktop/law/职业病危害预评价报告/长兴--预评（备案稿7-31）_extracted.json"

# 原报告旧版章节 → 生成新版章节 映射
MAP = [
    ("1 建设项目概况", "10.2.3", "工程分析"),
    ("2 职业病危害因素识别与评价", "10.2.5", "危害分析"),
    ("3 职业病危害防护措施评价", "10.2.6", "防护设施"),
    ("4 综合性评价", "10.2.3.7", "建筑卫生学"),
    ("5 职业病补充措施及建议", "10.2.11", "补充建议"),
    ("6 评价结论", "10.2.12", "结论"),
    ("10.1.1 生产工艺过程职业病危害因素", "10.2.5.1", "识别-工艺"),  # 识别子节
    ("10.4.3 本项目关键控制点", "10.2.10", "关键控制点"),
]


def original_section_text(prefix: str) -> str:
    """原报告章节文本 (按标题前缀收集)"""
    d = json.load(open(REPORT_JSON))
    out = []
    cur = "前置"
    for p in d["paragraphs"]:
        t = p.get("text", "") if isinstance(p, dict) else str(p)
        t = t.strip()
        if not t:
            continue
        if re.match(r"^(10\.\d+\.?\d*|1|2|3|4|5|6|7|8|9)\s+[\u4e00-\u9fff]", t) and len(t) < 25:
            cur = t
        if cur == prefix or cur.startswith(prefix):
            out.append(t)
    return "".join(out)


def main():
    p = get_project("demo-cx")
    states = (p["data"].get("section_states") or {})
    print("=" * 74)
    print("逐章内容分析: 生成(LLM正文) vs 原报告(对应章节) — 字数差距根因")
    print("=" * 74)
    for old_sec, new_sec, desc in MAP:
        gen_text = states.get(new_sec, {}).get("text", "")
        gen_chars = len(re.sub(r"\s+", "", gen_text))
        orig_text = original_section_text(old_sec)
        orig_chars = len(re.sub(r"\s+", "", orig_text))
        pct = f"{gen_chars/orig_chars*100:.0f}%" if orig_chars else "—"
        print(f"\n【{desc}】{old_sec[:20]} (原{orig_chars}字) vs {new_sec} (生成{gen_chars}字) 占{pct}")
        # 原报告分节结构 (看角度)
        subs = re.findall(r"(\d+\.\d+\.?\d*[\.\d]*)\s+[\u4e00-\u9fff]{2,}", orig_text)
        subs = list(dict.fromkeys(subs))[:6]
        if subs:
            print(f"  原报告子节角度: {', '.join(subs)}")
        # 生成内容概要
        if gen_text:
            first = gen_text.strip().split("。")[0]
            print(f"  生成内容: {first[:60]}...")


if __name__ == "__main__":
    main()
