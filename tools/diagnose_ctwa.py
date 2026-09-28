"""诊断: 表25 的 ctwa 到底走在哪个分支 → 为什么取到接触时间

复现 report_parser 的列定位 + 兜底逻辑, 打印中间值。
"""
import re
import sys
from pathlib import Path

import docx

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

DOC = Path.home() / "Desktop/长兴合成树脂/材料包_生成报告用/04_原有项目_现状评价报告.docx"


def main():
    d = docx.Document(str(DOC))
    tb = None
    for t in d.tables:
        txt = "\n".join(" ".join(c.text.strip() for c in r.cells) for r in t.rows)
        if "聚乙烯粉尘" in txt and "CTWA" in txt:
            tb = t
            break
    if tb is None:
        print("未找到表25")
        return 1

    # 复现 report_parser 的表头/行读取 (它用 tb[0] 做表头, tb[1:] 做数据)
    rows = [[c.text.replace("\n", " ").strip() for c in r.cells] for r in tb.rows]
    head = [str(c).replace("\n", "").strip() for c in rows[0]]
    print("=== 表头 (rows[0]) ===")
    for i, h in enumerate(head):
        print(f"   [{i}] {h!r}")
    print()

    # 复现列定位
    _ctwa_col = -1
    for i, h in enumerate(head):
        if "CTWA" in h:
            _ctwa_col = i
            break
    print(f"CTWA 列定位: {_ctwa_col}  → head[{_ctwa_col}]={head[_ctwa_col]!r}"
          if _ctwa_col >= 0 else "CTWA 列定位: 未命中")
    _factor_col = -1
    for i, h in enumerate(head):
        if ("粉尘种类" in h) or ("毒物种类" in h) or ("危害因素" in h) or ("检测项目" in h):
            _factor_col = i
            break
    print(f"factor 列定位: {_factor_col} → head[{_factor_col}]={head[_factor_col]!r}"
          if _factor_col >= 0 else "factor 列定位: 未命中")
    print()

    # 找聚乙烯粉尘行
    for ri, cells in enumerate(rows):
        if any("聚乙烯粉尘" in c for c in cells):
            print(f"=== 聚乙烯粉尘 行 (rows[{ri}]) ===")
            for i, c in enumerate(cells):
                tag = ""
                if i == _ctwa_col:
                    tag = "  ← CTWA 列"
                if i == _factor_col:
                    tag = "  ← factor 列"
                print(f"   [{i}] {c!r}{tag}")
            print()
            v = cells[_ctwa_col] if 0 <= _ctwa_col < len(cells) else ""
            ok = bool(re.fullmatch(r"([<>≤≥]?\s*)?\d+(\.\d+)?", v))
            print(f"CTWA 列取值: {v!r} → 正则匹配 {ok}")
            if not ok:
                print("→ CTWA 列值不匹配正则! 走兜底分支:")
                for c in cells[2:]:
                    if re.fullmatch(r"([<>≤≥]?\s*)?\d+(\.\d+)?", c):
                        print(f"   兜底取到第一个数字单元格: {c!r}  ← 这就是 bug")
                        break
            return 0
    print("未找到聚乙烯粉尘数据行")
    return 1


if __name__ == "__main__":
    sys.exit(main())
