"""盘点真实材料里的字段 vs 提取器实际产出的字段

目标: 分清"材料里有但我们没提取" vs "材料真没有"
重点: C17 类比检测报告 (detections 的 sio2/wd 等分级字段来源)
"""
import re
import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

PACKS = {
    "长兴": Path.home() / "Desktop/长兴合成树脂/材料包_生成报告用",
    "新泰": Path.home() / "Desktop/新泰预评价/材料包_生成报告用",
}


def find(pack, kw):
    if not pack.exists():
        return None
    for p in pack.rglob("*"):
        if kw in p.name and p.suffix.lower() == ".pdf":
            return p
    return None


def dump_tables(pdf_path, max_pages=8, max_tables=4):
    """抽 PDF 里的表格 (pdfplumber) — 看真实列名"""
    out = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for pi, page in enumerate(pdf.pages[:max_pages]):
                for ti, tb in enumerate(page.extract_tables() or []):
                    if not tb or len(tb) < 2:
                        continue
                    hdr = [str(c or "").replace("\n", "").strip() for c in tb[0]]
                    # 只要像检测表的 (含因素/浓度/限值 类列名)
                    joined = "".join(hdr)
                    if not re.search(r"因素|项目|浓度|限值|结果|CTWA|CSTEL|检测", joined):
                        continue
                    out.append({"page": pi + 1, "hdr": hdr, "rows": tb[1:4],
                                "nrows": len(tb)})
                    if len(out) >= max_tables:
                        return out
    except Exception as e:
        return [{"err": f"{type(e).__name__}: {e}"}]
    return out


def main():
    for name, pack in PACKS.items():
        print("=" * 80)
        print(f"【{name}】{pack}")
        if not pack.exists():
            print("  材料包不存在")
            continue
        for kw, label in (("检测", "类比检测报告 (C17)"),
                          ("体检", "职业健康检查 (C12)")):
            f = find(pack, kw)
            if not f:
                print(f"  {label}: 未找到")
                continue
            print(f"\n  ── {label}: {f.name}")
            tabs = dump_tables(f)
            for t in tabs:
                if "err" in t:
                    print(f"     ERR {t['err']}")
                    continue
                print(f"     p{t['page']} ({t['nrows']}行) 表头: {t['hdr']}")
                for r in t["rows"][:2]:
                    print(f"        {[str(c or '')[:18] for c in r]}")


if __name__ == "__main__":
    main()
