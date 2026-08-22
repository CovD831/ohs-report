"""逐章内容分析 — 生成 vs 原报告 (字数差距的根因, 不是数字数)

方法:
  1. 原报告按章节标题切分段落 (10.x / 1 / 2 ...)
  2. 生成报告按 docx 章节切分 (10.2.x 标题)
  3. 每章对比: 字数 / 内容点 / 角度差异
关键: 判断差距是 [详略差异](同一信息, 表述长短)
     还是 [角度差异](原报告有我们没写的角度)
"""
import json
import re
from pathlib import Path

from docx import Document

REPORT_JSON = "/Users/abaaba/Desktop/law/职业病危害预评价报告/长兴--预评（备案稿7-31）_extracted.json"
GEN_DOCX = Path("data/report_demo-cx.docx")


def extract_original_sections() -> dict:
    """原报告: 按章节标题切分段落"""
    d = json.load(open(REPORT_JSON))
    sections = {}
    cur = "前置"
    for p in d["paragraphs"]:
        t = p.get("text", "") if isinstance(p, dict) else str(p)
        t = t.strip()
        # 章节标题识别 (10.x / 1 / 2 开头短行)
        if re.match(r"^(10\.\d+\.?\d*|1|2|3|4|5|6|7|8|9)\s+[\u4e00-\u9fff]", t) and len(t) < 25:
            cur = t[:20]
            sections.setdefault(cur, [])
            continue
        sections.setdefault(cur, []).append(t)
    # 转字数
    out = {}
    for sec, paras in sections.items():
        txt = "".join(paras)
        out[sec] = {"chars": len(re.sub(r"\s+", "", txt)), "paras": len(paras), "sample": txt[:60]}
    return out


def extract_generated_sections() -> dict:
    """生成报告: docx 按 10.2.x 标题切分 (跳过目录页—标题重复)"""
    doc = Document(str(GEN_DOCX))
    sections = {}
    cur = "前置"
    titles_seen = set()
    for p in doc.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        if re.match(r"^10\.2\.\d+\.?\d*\s", t) and len(t) < 30:
            # 目录页标题重复 (第二次出现才进入内容)
            if t in titles_seen:
                cur = t[:20]
                sections.setdefault(cur, [])
                continue
            titles_seen.add(t)
            continue  # 目录条目不收集内容
        if cur == "前置" or cur == "目  录":
            continue
        sections.setdefault(cur, []).append(t)
    out = {}
    for sec, paras in sections.items():
        txt = "".join(paras)
        out[sec] = {"chars": len(re.sub(r"\s+", "", txt)), "paras": len(paras)}
    return out


def analyze():
    orig = extract_original_sections()
    gen = extract_generated_sections()
    print("=" * 72)
    print("逐章内容分析: 原报告 vs 生成报告 (字数差距根因)")
    print("=" * 72)
    print(f"{'原报告章节':22s} {'原字数':>6s} {'生成字数':>6s} {'占比':>6s}  内容点")
    for sec, info in orig.items():
        g = gen.get(sec, gen.get("前置", {"chars": 0}))
        pct = f"{g['chars']/info['chars']*100:.0f}%" if info['chars'] else "—"
        if info["chars"] > 100:  # 只列有内容的章
            print(f"{sec:22s} {info['chars']:6d} {g['chars']:6d} {pct:>6s}  {info['sample'][:40]}")


if __name__ == "__main__":
    analyze()
