"""量化: 材料里有多少内容是"扫描件" (无文本层) → 提取器永远取不到

判据: pdfplumber 抽不出文本 且 页面含图片 → 扫描件
这类文件必须 OCR 才能提取, 否则静默丢失。
"""
import sys
from pathlib import Path

import pdfplumber

PACKS = {
    "长兴": Path.home() / "Desktop/长兴合成树脂/材料包_生成报告用",
    "新泰": Path.home() / "Desktop/新泰预评价/材料包_生成报告用",
}


def scan_pdf(p, max_pages=6):
    """返回 (总页数, 抽样页文本字符数, 是否扫描件)"""
    try:
        with pdfplumber.open(str(p)) as pdf:
            n = len(pdf.pages)
            chars = 0
            imgs = 0
            for pg in pdf.pages[:max_pages]:
                chars += len(pg.extract_text() or "")
                imgs += len(pg.images or [])
            return n, chars, imgs
    except Exception as e:
        return 0, -1, 0


def main():
    print("=" * 86)
    print(f"{'文件':52} {'页':>4} {'抽样字符':>8} {'图':>4}  判定")
    print("-" * 86)
    total = scanned = 0
    for name, pack in PACKS.items():
        if not pack.exists():
            continue
        print(f"\n【{name}】")
        for p in sorted(pack.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() not in (".pdf", ".docx", ".doc", ".xlsx", ".csv", ".txt"):
                continue
            total += 1
            if p.suffix.lower() == ".pdf":
                n, chars, imgs = scan_pdf(p)
                is_scan = (chars == 0 and imgs > 0) or chars <= 5
                if is_scan:
                    scanned += 1
                tag = "★扫描件(需OCR)" if is_scan else "有文本层"
                print(f"  {p.name[:50]:52} {n:4} {chars:8} {imgs:4}  {tag}")
            else:
                print(f"  {p.name[:50]:52} {'-':>4} {'-':>8} {'-':>4}  {'可直接解析'}")
    print()
    print("=" * 86)
    print(f"总文件 {total} | 扫描件(PDF) {scanned}")


if __name__ == "__main__":
    main()
