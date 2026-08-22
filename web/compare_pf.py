"""浦发报告对比 — 生成 vs 浦发原报告 (字数/占比)

对比维度: 生成(existing) 与 长兴报告相同方法:
  原报告章节字数(按1-9镜像) vs 生成(LLM正文)
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PF_REPORT = "/Users/abaaba/Desktop/law/职业病危害预评价报告/预评新-  浦发热电（备案稿）_extracted.json"
PID = "e6e3853629"


def count_gen() -> dict:
    from web.projects_db import get_project
    p = get_project(PID)
    st = p["data"].get("section_states", {})
    out = {}
    for k, v in st.items():
        if v.get("state") == "generated" and len(v.get("text", "")) > 50:
            out[k] = len(v["text"])
    return out


def count_orig() -> dict:
    """浦发原报告: 按 1-9 计数 (序号开头标题段落)"""
    d = json.load(open(PF_REPORT))
    out = {}
    cur = "0"
    for p in d["paragraphs"]:
        t = p.get("text", "") if isinstance(p, dict) else str(p)
        t = t.strip()
        m = re.match(r"^(\d+)\s+[\u4e00-\u9fff]{2,20}$", t)
        if m:
            cur = m.group(1)
            out.setdefault(cur, 0)
        elif cur in out:
            out[cur] += len(t)
    return out


def main():
    gen = count_gen()
    orig = count_orig()
    print("=" * 70)
    print(f"浦发对比: 生成({len(gen)}单元/{sum(gen.values())}字) vs 原报告({len(orig)}章/"
          f"{sum(orig.values())}字)")
    print("=" * 70)
    gen_main = sum(v for k, v in gen.items() if "." not in k)
    print(f"一级9章总字数: {gen_main} | 原报告总(含小节): {sum(orig.values())}")
    print(f"\n对比: 生成总 {sum(gen.values())} / 原 {sum(orig.values())} = "
          f"{sum(gen.values()) / (sum(orig.values()) or 1) * 100:.0f}%")


if __name__ == "__main__":
    main()
