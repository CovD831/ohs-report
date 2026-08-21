"""PDF OCR — 扫描件转文本 (清洗阶段公共能力)

用法:
    python3 knowledge/ocr_pdf.py <input.pdf> [output.txt] [--dpi 300]
输出: 纯文本 (每页以 ==== 第N页 ==== 分隔)
依赖: pymupdf + tesseract(chi_sim)
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf


def ocr_pdf(pdf_path: Path, dpi: int = 300) -> str:
    doc = pymupdf.open(str(pdf_path))
    pages = []
    with tempfile.TemporaryDirectory() as td:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            img = Path(td) / f"p{i:03d}.png"
            pix.save(str(img))
            r = subprocess.run(
                ["tesseract", str(img), "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                capture_output=True, text=True, timeout=120)
            text = r.stdout or ""
            pages.append(f"==== 第{i + 1}页 ====\n{text}")
    return "\n".join(pages)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path, nargs="?", default=None)
    ap.add_argument("--dpi", type=int, default=300)
    args = ap.parse_args()
    out = args.output or args.input.with_suffix(".ocr.txt")
    text = ocr_pdf(args.input, args.dpi)
    out.write_text(text, encoding="utf-8")
    print(f"OCR 完成: {args.input.name} ({len(text)} 字符) -> {out}")
