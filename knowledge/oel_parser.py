"""GBZ 2.1—2019 OEL 限值表解析器 — 判定引擎基准数据源

解析对象 (acquire/raw/nhc_gbz/pdfs/GBZ_2.1_2019.pdf, 53页):
  表1 页7-31   化学有害因素 (9列: 序号/中文名/英文名/CAS/MAC/PC-TWA/PC-STEL/临界效应/备注)
  表2 页32-35  粉尘 (8列: 序号/中文名/英文名/CAS/总尘/呼尘/临界效应/备注)
  表3 页36-38  生物监测指标 BEI (本次不解析, 需独立表, 见 TODO)

多行条目处理 (PDF 排版把子条目/上标拆成换行):
  矽尘(40)  : 3档 SiO2 含量 → 拆3行, 档位入 conditions
  麻尘(22)  : 亚麻/黄麻/苎麻 → 拆3行, 种类入 conditions
  石棉(34)  : 粉尘 0.8 mg/m3 + 纤维 0.8 f/mL → 拆2行 (单位不同!)
  上标碎片:  'SiO 含量≤50 %\n2' → 'SiO2 含量≤50 %' (2 是 SiO2 下标)

修改单叠加:
  第1号修改单 (2022-11): 表1 序号12 苯 PC-TWA 6→3, PC-STEL 10→6
  第2号修改单 (2025-05-01): 表1 增加序号359 乙草胺 PC-TWA 0.12

输出: data/ohs.db → oel_limit (verified=0) + knowledge/oel_multi_review.jsonl (人工复核清单)
用法: python3 -m knowledge.oel_parser
"""
import json
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect, insert  # noqa: E402

PDF = Path(__file__).resolve().parent.parent / "acquire/raw/nhc_gbz/pdfs/GBZ_2.1_2019.pdf"
T1_PAGES = range(6, 31)    # 表1: 页7-31
T2_PAGES = range(31, 35)   # 表2: 页32-35
REVIEW_OUT = Path(__file__).resolve().parent / "oel_multi_review.jsonl"

DASH = ("―", "—", "-", "－")


def _x(v):
    """单值清洗: 无值符号 → None; 换行/多余空白压缩"""
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    if s in DASH or s == "":
        return None
    return s


def _merge_sup(prev: str, ln: str) -> str:
    """上标/下标碎片合并: 'SiO ' 或行尾 'SiO' + '2' → 'SiO2' (化学式下标语义)

    仅在名称列调用; 值列的行都是独立子项值, 不得合并。
    """
    if re.search(r"SiO\s*$", prev):          # 前行行尾是 SiO
        return re.sub(r"SiO\s*$", "SiO2", prev)
    if re.search(r"SiO\s", prev):            # 前行含 SiO (后接空格)
        return re.sub(r"SiO\s", "SiO2", prev, count=1)
    return prev + ln


def _norm_lines(cell, merge_sup: bool = False) -> list[str]:
    """单元格文本 → 清洗后的行列表 (名称列可选上标合并)"""
    if cell is None:
        return []
    lines = [ln.strip() for ln in str(cell).split("\n")]
    merged = []
    for ln in lines:
        if not ln:
            continue
        if (merge_sup and merged
                and re.fullmatch(r"[0-9a-zA-Z]{1,2}", ln) and not ln.isalpha()):
            merged[-1] = _merge_sup(merged[-1], ln)
        else:
            merged.append(ln)
    return merged


def _row_is_data(row):
    if not row or row[0] is None:
        return False
    return bool(re.fullmatch(r"\d+", str(row[0]).strip()))


def _split_multi(row, val_cols: dict, effect_idx: int = 7, note_idx: int = 8) -> list[dict] | None:
    """多子条目拆分: 值列行数>1 → 按子项拆行 (名称尾部取子名, 其余为主名)

    返回 None 表示无需拆分 (单条目)。拆出的子行 dict 带 _multi=1 标记。
    """
    cols = {}
    for name, idx in val_cols.items():
        cols[name] = _norm_lines(row[idx])
    n_vals = max((len(v) for v in cols.values()), default=0)
    if n_vals <= 1:
        return None
    # 子名: 名称列尾部 n_vals 行 (如 亚麻/黄麻/苎麻); 主名: 其余行
    # 名称列做上标合并 (SiO2), 值列不做 (独立的子项值)
    name_lines = _norm_lines(row[1], merge_sup=True)
    main_name = " ".join(name_lines[: len(name_lines) - n_vals]) if len(name_lines) > n_vals else name_lines[0]
    subs = name_lines[-n_vals:] if len(name_lines) >= n_vals else [""] * n_vals
    en_lines = _norm_lines(row[2], merge_sup=True)
    out = []
    for i in range(n_vals):
        row_i = {"seq": int(row[0]), "multi": 1}
        row_i["factor_name"] = main_name
        row_i["conditions"] = subs[i]
        # 子名含括号说明切分错位 (如 '玻' 被拆成子名) → 标记人工复核
        if any(c in subs[i] for c in "（）()"):
            row_i["name_weird"] = 1
        row_i["english_name"] = _x(en_lines[i] if i < len(en_lines) else None)
        row_i["cas"] = _x(row[3])
        for name, idx in val_cols.items():
            lines = _norm_lines(row[idx])
            row_i[name] = _x(lines[i] if i < len(lines) else None)
        row_i["effect"] = _x(row[effect_idx])
        row_i["note"] = _x(row[note_idx]) if len(row) > note_idx else None
        out.append(row_i)
    return out


def parse_table1() -> list[dict]:
    """表1 化学有害因素 (值列: mac/4 pc_twa/5 pc_stel/6)"""
    doc = pymupdf.open(str(PDF))
    rows = []
    for p in T1_PAGES:
        tabs = doc[p].find_tables()
        if not tabs.tables:
            continue
        for r in tabs.tables[0].extract():
            if not _row_is_data(r):
                continue
            multi = _split_multi(r, {"mac": 4, "pc_twa": 5, "pc_stel": 6})
            if multi:
                rows.extend(multi)
            else:
                rows.append({
                    "seq": int(r[0]), "multi": 0,
                    "factor_name": _x(r[1]), "english_name": _x(r[2]), "cas": _x(r[3]),
                    "mac": _x(r[4]), "pc_twa": _x(r[5]), "pc_stel": _x(r[6]),
                    "effect": _x(r[7]), "note": _x(r[8]),
                })
    doc.close()
    return rows


def parse_table2() -> list[dict]:
    """表2 粉尘 (值列: 总尘/4 呼尘/5)"""
    doc = pymupdf.open(str(PDF))
    rows = []
    for p in T2_PAGES:
        tabs = doc[p].find_tables()
        if not tabs.tables:
            continue
        for r in tabs.tables[0].extract():
            if not _row_is_data(r):
                continue
            multi = _split_multi(r, {"pc_twa_total": 4, "pc_twa_resp": 5}, effect_idx=6, note_idx=7)
            if multi:
                rows.extend(multi)
            else:
                rows.append({
                    "seq": int(r[0]), "multi": 0,
                    "factor_name": _x(r[1]), "english_name": _x(r[2]), "cas": _x(r[3]),
                    "pc_twa_total": _x(r[4]), "pc_twa_resp": _x(r[5]),
                    "effect": _x(r[6]), "note": _x(r[7]),
                })
    doc.close()
    return rows


def apply_amendments(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """应用第1/2号修改单, 返回 (更新后行, 日志)"""
    logs = []
    for r in rows:
        if r["factor_name"] == "苯" and r["seq"] == 12:
            logs.append(f"修改单1: 苯 {r['pc_twa']}/{r['pc_stel']} → 3/6")
            r["pc_twa"], r["pc_stel"] = "3", "6"
            r["effect"] = "神经系统损害；血液毒性"
            r["amended"] = "2022第1号修改单"
            break
    if not any(r["factor_name"] == "乙草胺" for r in rows):
        rows.append({
            "seq": 359, "multi": 0, "factor_name": "乙草胺", "english_name": "Acetochlor",
            "cas": "34256-82-1", "mac": None, "pc_twa": "0.12", "pc_stel": None,
            "effect": "肝、肾损伤", "note": None, "amended": "2025第2号修改单",
        })
        logs.append("修改单2: 表1 新增 359 乙草胺 PC-TWA 0.12")
    return rows, logs


def write_review(rows: list[dict]):
    """多行条目(拆过/有疑点) → 人工复核清单"""
    with open(REVIEW_OUT, "w", encoding="utf-8") as f:
        for r in rows:
            if r.get("multi"):
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _value_unit(v: str | None, default_unit: str) -> tuple[str | None, str]:
    """拆出值中的单位: '0.8 f/mL' → ('0.8', 'f/mL'); 无单位 → (v, default)"""
    if v is None:
        return None, default_unit
    m = re.search(r"([0-9][^\s]*)\s*([a-zA-Z/μµ]+[^\\d]*)$", v)
    if m and m.group(2):
        return m.group(1), m.group(2)
    return v, default_unit


def to_oel_rows(t1: list[dict], t2: list[dict]) -> list[dict]:
    """表1/表2 → oel_limit 行 (化学按 物质×限值类型 展开; 粉尘 总尘/呼尘)"""
    out = []
    for r in t1:
        base = {
            "factor_name": r["factor_name"], "factor_type": "化学有害因素",
            "cas": r["cas"], "english_name": r["english_name"],
            "note": r["note"], "source_standard": "GBZ 2.1—2019", "verified": 0,
        }
        for typ, key in (("MAC", "mac"), ("PC-TWA", "pc_twa"), ("PC-STEL", "pc_stel")):
            v = r.get(key)
            if v is not None:
                out.append({**base, "oel_type": typ, "value": str(v),
                            "unit": "mg/m3", "conditions": r.get("conditions")})
    for r in t2:
        base = {
            "factor_name": r["factor_name"], "factor_type": "粉尘",
            "cas": r["cas"], "english_name": r["english_name"],
            "note": r["note"], "source_standard": "GBZ 2.1—2019", "verified": 0,
        }
        for typ, key in (("PC-TWA(总尘)", "pc_twa_total"), ("PC-TWA(呼尘)", "pc_twa_resp")):
            v = r.get(key)
            if v is not None:
                val, unit = _value_unit(str(v), "mg/m3")
                out.append({**base, "oel_type": typ, "value": val,
                            "unit": unit, "conditions": r.get("conditions")})
    return out


def main():
    t1 = parse_table1()
    t2 = parse_table2()
    t1, logs = apply_amendments(t1)
    print(f"表1(化学): {len(t1)} 条 (含修改单)")
    print(f"表2(粉尘): {len(t2)} 条")
    for l in logs:
        print(f"  {l}")
    write_review(t2 + t1)
    multi = [r for r in t1 + t2 if r.get("multi")]
    print(f"多子条目拆分: {len(multi)} 条 → knowledge/oel_multi_review.jsonl")
    rows = to_oel_rows(t1, t2)
    conn = connect()
    conn.execute("DELETE FROM oel_limit WHERE source_standard='GBZ 2.1—2019'")  # 幂等重建
    insert(conn, rows)
    cur = conn.execute(
        "SELECT factor_type, COUNT(DISTINCT factor_name), COUNT(*) FROM oel_limit "
        "WHERE source_standard='GBZ 2.1—2019' GROUP BY factor_type")
    print("入库 oel_limit:")
    for ft, nf, nr in cur.fetchall():
        print(f"  {ft}: {nf} 物质 / {nr} 行")
    for name in ("苯", "乙草胺", "矽尘", "麻尘", "石棉"):
        for r in conn.execute(
                "SELECT factor_name, oel_type, value, unit, COALESCE(conditions,''), note "
                "FROM oel_limit WHERE factor_name LIKE ? ORDER BY oel_type", (f"{name}%",)):
            print("  抽查:", " | ".join(str(x) for x in r))
    conn.close()


if __name__ == "__main__":
    main()
