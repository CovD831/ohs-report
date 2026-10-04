#!/usr/bin/env python3
"""从可研/项目申请报告提取「主要技术经济指标」表 (表1.2-11, 跨页)

目标: data/tech_econ.json — 供 section_filler 建「项目概况表」(表3.1-x 主要技术经济指标)
数据源: 材料包 C2_项目概况/*可研*.pdf (本项目特化, 非通用标准)
红线: 只提取原文有的; 提取不到留空 — 绝不编造数字

用法:
  .venv/bin/python tools/extract_tech_econ.py            # 提取并打印
  .venv/bin/python tools/extract_tech_econ.py --write    # 写入 data/tech_econ.json
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "tech_econ.json"

# 「主要技术经济指标」表头特征 (可研 表1.2-11)
_HDR = ("项目名称", "单位")
# 中文数字序号 一/二/.../十五
_CN_ORD = "一二三四五六七八九十"


def find_pdf(material_dir: str) -> str | None:
    """材料包内定位可研/项目申请报告 PDF"""
    pats = [
        f"{material_dir}/**/*可研*.pdf",
        f"{material_dir}/**/*申请报告*.pdf",
        f"{material_dir}/**/*项目申请*.pdf",
    ]
    for p in pats:
        hits = sorted(glob.glob(p, recursive=True))
        if hits:
            return hits[0]
    return None


def is_tech_econ_table(rows: list[list[str]]) -> bool:
    """判据: 表头含 项目名称+单位, 且正文含 '工程项目总投资' 或 '总投资'"""
    if not rows:
        return False
    flat = " ".join(" ".join(r) for r in rows[:3])
    if not all(k in flat for k in _HDR):
        return False
    body = " ".join(" ".join(r) for r in rows)
    return ("总投资" in body) or ("销售收入" in body) or ("定员" in body)


def norm_row(r: list) -> list[str]:
    return [(c or "").replace("\n", "").strip() for c in r]


def extract(pdf_path: str) -> dict:
    import pdfplumber

    items: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
        # 找所有含「主要技术经济指标」的页附近
        cand_pages = []
        for i in range(n):
            t = pdf.pages[i].extract_text() or ""
            if "主要技术经济指标" in t:
                cand_pages += [i, i + 1]
        cand_pages = sorted(set(p for p in cand_pages if 0 <= p < n))
        seen_tables = []
        for i in cand_pages:
            for tb in pdf.pages[i].extract_tables():
                rows = [norm_row(r) for r in tb if r]
                if not rows:
                    continue
                if is_tech_econ_table(rows):
                    seen_tables.append((i, rows))
        # 跨页拼接: 同名表头去重, 按页序合并
        merged: list[list[str]] = []
        for i, rows in seen_tables:
            for k, r in enumerate(rows):
                if k == 0 and r and "项目名称" in " ".join(r):
                    continue  # 表头
                if r and any(c for c in r):
                    merged.append(r)
    # 去重叠: 同一行若在 merged 出现两次(跨页重复), 保留首个
    out_rows, seen = [], set()
    for r in merged:
        key = tuple(r[:2])
        if key in seen:
            continue
        seen.add(key)
        out_rows.append(r)
    return {"source": pdf_path.split("materials/")[-1], "cols": ["序号", "项目名称", "单位", "数量", "备注"],
            "rows": out_rows}


def main():
    write = "--write" in sys.argv
    mat = ROOT / "data" / "materials"
    results = {}
    for mdir in sorted(mat.glob("*")):
        if not mdir.is_dir():
            continue
        pdf = find_pdf(str(mdir))
        if not pdf:
            print(f"  [跳过] {mdir.name}: 无可研 PDF")
            continue
        try:
            data = extract(pdf)
            results[mdir.name] = data
            print(f"  {mdir.name}: {len(data['rows'])} 行 ← {data['source']}")
            for r in data["rows"]:
                print("      ", " | ".join(r))
        except Exception as e:
            print(f"  [错误] {mdir.name}: {e}")
    if write and results:
        # 按项目 id 存
        allf = ROOT / "data" / "tech_econ.json"
        old = json.loads(allf.read_text(encoding="utf-8")) if allf.exists() else {}
        old.update(results)
        allf.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ 写入 {allf} ({len(results)} 项目)")
    elif results:
        print("\n[DRY-RUN] 加 --write 落盘")


if __name__ == "__main__":
    main()
