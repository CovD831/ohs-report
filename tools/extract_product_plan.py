#!/usr/bin/env python3
"""从可研/项目申请报告提取「扩产前后产能变化信息对照表」(表1.2-1)

目标: data/product_plan.json — 供 section_filler 建
      「本项目扩建前后全厂产品方案对比表」(复刻真稿 表8.1-3, 挂 3.1.6)
数据源: 材料包 C2_项目概况/*可研*.pdf (本项目特化, 非通用标准)
红线: 只提取原文有的; 提取不到留空 — 绝不编造数字

表结构 (可研 p15, 双层表头):
  序号 | 名称(产品·类型) | 安全生产许可证领证 | 单位 | 主要成分
       | 年产量吨/年(扩产前·扩产后) | 变化量 | 备注

用法:
  .venv/bin/python tools/extract_product_plan.py            # 提取并打印
  .venv/bin/python tools/extract_product_plan.py --write    # 写入 data/product_plan.json
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "product_plan.json"

# 表头特征
_HDR_A = "年产量"
_HDR_B = "扩产前"
_HDR_C = "扩产后"


def find_pdf(material_dir: str) -> str | None:
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


def _clean(c) -> str:
    """去换行/空白; 合并被 pdfplumber 拆开的数字 (如 '+1200\\n0' → '+12000')"""
    s = (c or "").replace("\n", "").replace(" ", "").strip()
    return s


def is_product_plan_table(rows: list[list[str]]) -> bool:
    """判据: 含「扩产前」「扩产后」+ 名称/变化量列"""
    if not rows:
        return False
    flat = " ".join(" ".join(str(c or "") for c in r) for r in rows[:3])
    return (_HDR_B in flat and _HDR_C in flat)


def extract(pdf_path: str) -> dict:
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
        cand = []
        for i in range(n):
            t = pdf.pages[i].extract_text() or ""
            if "扩产前后产能变化" in t or ("扩产前" in t and "扩产后" in t):
                cand.append(i)
        rows_out: list[list[str]] = []
        for i in sorted(set(cand)):
            for tb in pdf.pages[i].extract_tables():
                raw = [[_clean(c) for c in r] for r in tb if r]
                if not is_product_plan_table(raw):
                    continue
                for r in raw:
                    # 跳过表头行 / 纯表头片段
                    joined = "".join(r)
                    if "序号" in joined and ("名称" in joined or "单位" in joined):
                        continue
                    if joined in ("年产量吨/年", "扩产前扩产后", "扩产前", "扩产后"):
                        continue
                    # 数据行: 需有「吨/年」单位列
                    if "吨/年" not in joined:
                        continue
                    rows_out.append(r)
    # 前向填充「产品名称」「安全生产许可证领证」
    #   源表: 「产品名称」跨整个产品组纵向合并(仅首行有值); 「领证」同理。
    #   注意 序号 只在"产品名称为空"的首行出现的场景不成立 —— 序号标记的是具体规格行,
    #   名称才是分组键 → 一律: 名称非空则刷新, 空则继承上一个非空值。
    _last_name = _last_lic = ""
    for r in rows_out:
        if len(r) < 10:
            r += [""] * (10 - len(r))
        _nm = str(r[1]).strip()
        if _nm:
            _last_name = _nm
            _last_lic = str(r[3]).strip()  # 新分组同时刷新领证状态
        else:
            r[1] = _last_name
            if not str(r[3]).strip():
                r[3] = _last_lic
    return {
        "source": pdf_path.split("materials/")[-1],
        "cols": ["序号", "产品名称", "产品类型", "安全生产许可证领证", "单位",
                 "主要成分", "扩产前", "扩产后", "变化量", "备注"],
        "rows": rows_out,
    }


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
        allf = OUT
        old = json.loads(allf.read_text(encoding="utf-8")) if allf.exists() else {}
        old.update(results)
        allf.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ 写入 {allf} ({len(results)} 项目)")
    elif results:
        print("\n[DRY-RUN] 加 --write 落盘")


if __name__ == "__main__":
    main()
