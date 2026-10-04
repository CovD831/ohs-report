#!/usr/bin/env python3
"""从可研提取「主要新增设备表」(表1.2-3) —— 本项目新增设备的口径

数据源: 材料包 C2_项目概况/*可研*.pdf  (本项目特化)
输出: data/new_equipment.json  {pid: {count, power_kw, amount, imported, domestic}}

⚠ 与 data/ohs.db 的 equipment_detail (172 行, 全厂现有设备) **口径不同**:
   - equipment_detail  = 全厂现有装置设备清单 (源: 现状评价), 用于 表3.6-2
   - 表1.2-3 (本脚本)  = 本项目**新增**设备 27 台(套), 用于 3.1.6 正文台套数
   混用会把"全厂 172 台"错说成"本项目新增 172 台" —— 红线: 不得混淆口径。
"""
import os, re, json, sys, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAT = os.path.join(ROOT, "data", "materials")
OUT = os.path.join(ROOT, "data", "new_equipment.json")


def find_kyspdf(pid):
    base = os.path.join(MAT, pid)
    if not os.path.isdir(base):
        return None
    hits = []
    for d in os.listdir(base):
        if "概况" in d or "申请" in d:
            hits += glob.glob(os.path.join(base, d, "*.pdf"))
    hits = [h for h in hits if "可研" in os.path.basename(h) or "申请报告" in os.path.basename(h)]
    return hits[0] if hits else None


# 序号 名称 [功率] 数量 单价(≥3位) 总价(≥3位) 国别
#   数量为 1~2 位; 单价/总价金额必 ≥3 位 → 用位数区分, 避免把功率当成数量
ROW = re.compile(
    r'^\s*(\d+)\s+(.+?)\s+(?:\d+(?:\.\d+)?\s+)?(\d{1,3})\s+(\d{3,})\s+(\d{3,})\s+(国产|进口)\s*$')


def parse(pdf_path):
    import pdfplumber
    rows = []
    cap = None
    with pdfplumber.open(pdf_path) as pdf:
        in_tbl = False
        for pg in pdf.pages:
            t = pg.extract_text() or ""
            if "主要新增设备" in t:
                in_tbl = True
                m = re.search(r'新增主要设备\s*(\d+)\s*台\s*[（(]\s*套\s*[)）]', t)
                if m:
                    cap = {"count": int(m.group(1))}
                m2 = re.search(r'总装机容量\s*([\d.]+)\s*kW', t)
                if m2:
                    cap = cap or {}
                    cap["power_kw"] = float(m2.group(1))
            if not in_tbl:
                continue
            # 先扫本页数据行 (表1.2-4 引导语与合计行可能同页 → 必须先消费行)
            for ln in t.split("\n"):
                m = ROW.match(ln)
                if m:
                    rows.append({"seq": int(m.group(1)), "name": m.group(2).strip(),
                                 "qty": int(m.group(3)), "price": int(m.group(4)),
                                 "total": int(m.group(5)), "origin": m.group(6)})
            if "利用原有公用工程" in t or "表 1.2-4" in t or "表1.2-4" in t:
                # 合计行: 功率 台套数 总金额
                m = re.search(r'合计\s+([\d.]+)\s+(\d+)\s+([\d]+)', t)
                if m:
                    cap = cap or {}
                    cap["power_kw"] = float(m.group(1))
                    cap["count"] = int(m.group(2))
                    cap["amount"] = int(m.group(3))
                break
    imp = sum(r["qty"] for r in rows if r["origin"] == "进口")
    dom = sum(r["qty"] for r in rows if r["origin"] == "国产")
    cap = cap or {}
    cap.setdefault("count", sum(r["qty"] for r in rows))
    cap.setdefault("amount", sum(r["total"] for r in rows))
    cap["imported"] = imp
    cap["domestic"] = dom
    cap["rows"] = len(rows)
    return cap


def main():
    write = "--write" in sys.argv
    out = {}
    if os.path.exists(OUT):
        try:
            out = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            out = {}
    for pid in sorted(os.listdir(MAT)):
        if not os.path.isdir(os.path.join(MAT, pid)):
            continue
        pdfp = find_kyspdf(pid)
        if not pdfp:
            print(f"[跳过] {pid}: 无可研 PDF")
            continue
        try:
            info = parse(pdfp)
        except Exception as e:
            print(f"[错误] {pid}: {e}")
            continue
        if not info.get("count"):
            print(f"[跳过] {pid}: 未解析到新增设备表")
            continue
        out[pid] = info
        print(f"{pid}: 新增 {info['count']} 台(套), {info.get('power_kw')}kW, "
              f"{info.get('amount')} 元, 进口{info['imported']}/国产{info['domestic']} (行{info['rows']})")
    if write:
        json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n✅ 写入 {OUT} ({len(out)} 项目)")


if __name__ == "__main__":
    main()
