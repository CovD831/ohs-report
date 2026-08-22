"""两篇报告章节结构对比 — 长兴 vs 浦发热电

目的:
  1. 提取两篇报告的全部章节标题 (多层级)
  2. 对比: 大章节是否一致? 小章节差异?
  3. 结论: 章节体系怎么设计 (大章节框架+小章节按项目)
"""
import json
import re

REPORTS = {
    "长兴(化工)": "/Users/abaaba/Desktop/law/职业病危害预评价报告/长兴--预评（备案稿7-31）_extracted.json",
    "浦发(焚烧发电)": "/Users/abaaba/Desktop/law/职业病危害预评价报告/预评新-  浦发热电（备案稿）_extracted.json",
}


def extract_headings(path: str, max_heading: int = 4) -> dict:
    """提取报告章节标题 → {编号: 标题}"""
    d = json.load(open(path))
    headings = {}
    for p in d["paragraphs"]:
        t = p.get("text", "") if isinstance(p, dict) else str(p)
        t = t.strip().replace("\n", " ")
        # 匹配 10.1.1.1 或 1. 或 1.1 或 1 或 1.1.1 编号开头
        m = re.match(r"^(\d+(?:\.\d+){0,3})?\s*[\s\u3000]?([\u4e00-\u9fff（）()、·]{2,25})$", t)
        if m and m.group(1):
            num = m.group(1)
            # 只取章节编号(非数字内容)
            if num and len(num) <= 8 and num.count(".") <= 4:
                # 过滤纯数字行(表内)
                if not m.group(2).isdigit():
                    headings[num] = m.group(2)
    return headings


def main():
    all_heads = {}
    for name, path in REPORTS.items():
        h = extract_headings(path)
        all_heads[name] = h
        print(f"=== {name}: {len(h)} 个标题 ===")
    # 大章节 (一级: 纯数字或 10.x)
    print("\n" + "=" * 64)
    print("一、大章节对比 (顶层)")
    print("=" * 64)
    long = set()
    pud = set()
    for n in all_heads["长兴(化工)"]:
        if n.count(".") == 0:
            long.add(n)
    for n in all_heads["浦发(焚烧发电)"]:
        if n.count(".") == 0:
            pud.add(n)
    print(f"  长兴: {sorted(long, key=lambda x: (len(x), x))}")
    print(f"  浦发: {sorted(pud, key=lambda x: (len(x), x))}")
    # 二级章节对比
    print("\n" + "=" * 64)
    print("二、二级章节对比 (x.x)")
    print("=" * 64)
    long2 = {n: t for n, t in all_heads["长兴(化工)"].items() if n.count(".") == 1}
    pud2 = {n: t for n, t in all_heads["浦发(焚烧发电)"].items() if n.count(".") == 1}
    for n in sorted(long2, key=lambda x: (len(x), x)):
        print(f"  长兴 {n:6s} {long2[n][:20]}")
    print("  ----")
    for n in sorted(pud2, key=lambda x: (len(x), x)):
        print(f"  浦发 {n:6s} {pud2[n][:20]}")
    # 三级
    print("\n" + "=" * 64)
    print("三、三级章节差异 (x.x.x) — 前15")
    print("=" * 64)
    long3 = [n for n in all_heads["长兴(化工)"] if n.count(".") == 2]
    pud3 = [n for n in all_heads["浦发(焚烧发电)"] if n.count(".") == 2]
    print(f"  长兴三级: {len(long3)} 个, 示例: {long3[:8]}")
    print(f"  浦发三级: {len(pud3)} 个, 示例: {pud3[:8]}")


if __name__ == "__main__":
    main()
