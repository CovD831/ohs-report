#!/usr/bin/env python3
"""可研报告「主要设备一览表」提取器 — 跨页多页表格, 双层表头, 分组行

材料实证 (长兴 可研 p162~189, 28 页连续):
  表头(双层, 每页重复):
    车间 | 序号 | 位号 | 设备名称 | 规格型号 | 数量 | 材质 | 操作参数[温度℃|压力MPa|反应介质] | 停用与变更情况
  分组行: 第1列有值(如"不饱和聚酯树脂"/"生产厂房(二)公辅设施"), 其余空
  数据行: 序号为纯数字

输出 (本次按用户拍板 → 真稿 8 列口径):
  序号 | 设备名称 | 规格 | 材质 | 数量/台 | 操作条件(温度) | 内部物料(反应介质) | 备注(变更情况)
  + 分组标题行 (整行只填设备名称列) 与真稿 316 行结构一致

注意:
  - 规格型号里含换行(如"SUS 316\nΦ3410")→ 需要合并
  - 操作参数是 3 列合并表头, 拆为 温度/压力/介质
  - 变更情况 = "新增"/"无"/"停用" → 进备注列
"""
import sys, re, json, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 定位表头行的关键词 (跨页重复, 用第一列判定更稳)
HDR1 = ["车间", "序", "位号", "设备名称", "规格", "数量", "材质", "操作参数", "变更"]
HDR2 = ["温度", "压力", "反应介质"]


def _cells(row):
    """单元格清洗: 去换行拼接 + 去空格"""
    out = []
    for c in row:
        c = (c or "").replace("\n", "").strip()
        out.append(c)
    return out


def _is_header(cs):
    j = "".join(cs)
    if ("设备名称" in j or "设备名" in j) and ("材质" in j or "规格" in j):
        return True
    # 第二层表头残行: 只有 温度℃/压力MPa/反应介质 (跨页重复)
    if re.search(r"温度|压力|反应介质", j) and not re.search(r"[\u4e00-\u9fa5]{4,}", j.replace("反应介质", "")):
        return True
    return False


def _is_group(cs):
    """分组行: **序号列位置为空**, 且非空列里出现产线/车间名

    关键: 不能用「任意列无数字」判定 —— 数据行首列(车间)常为空,
    但序号列必有数字。故先定位序号列位置, 再看它是否为空。

    实证 (长兴可研 p162+):
      分组 ['主厂房','不饱和聚酯树脂','','',...]   序号列(第2列)空
      数据 ['', '1', 'R-2A01', ...]                序号列(第2列)='1'
    """
    ne = [c for c in cs if c]
    if not ne:
        return False
    # 只要 cs 里有纯数字 → 数据行 (序号/数量)
    if any(re.fullmatch(r"\d+", c) for c in cs):
        return False
    s = "".join(ne)
    # 续行特征: 含「：」或「、」+「：」 → 是反应介质/操作条件的换行续写, 不是分组名
    if "：" in s or ":" in s:
        return False
    # 分组名: 中文, 无标点, 长度 3~40
    if not re.search(r"[\u4e00-\u9fa5]{3,}", s):
        return False
    if len(s) > 40:
        return False
    return True


def extract(pdf_path: str, first_page: int, last_page: int) -> dict:
    import pdfplumber
    rows, pages_used, warn = [], [], []
    cur_group = None
    emitted = set()                 # 已输出的分组标题 (跨页重复只留首次)
    prev = None                     # 上一条数据行 (用于判断是否被跨页切断)
    with pdfplumber.open(pdf_path) as pdf:
        for pi in range(first_page - 1, min(last_page, len(pdf.pages))):
            pg = pdf.pages[pi]
            tabs = pg.extract_tables() or []
            used = False
            for tab in tabs:
                for raw in tab:
                    cs = _cells(raw)
                    if not any(cs):
                        continue
                    if _is_header(cs):
                        continue
                    if _is_group(cs):
                        ne = [c for c in cs if c]
                        g = ne[-1] if len(ne) > 1 else ne[0]   # 两列时取后列(产线名)
                        # 跨页重复的分组标题只保留首次 (实证: p162 之后每页重复"主厂房/不饱和聚酯树脂")
                        if g != cur_group and g not in emitted:
                            cur_group = g
                            emitted.add(g)
                            rows.append({"group": g})
                            used = True
                        continue
                    # 数据行: 找序号列 (纯数字) — 可研布局序号在第2列, 真稿在第1列
                    idx_i = None
                    for i, c in enumerate(cs[:3]):
                        if re.fullmatch(r"\d+", c):
                            idx_i = i
                            break
                    if idx_i is None:
                        # 可能是上一条的续行(规格换行被切) → 并入 prev
                        if prev is not None and any(cs):
                            tail = "".join(x for x in cs if x)
                            if tail and not re.search(r"[，。；]", tail):
                                prev["spec"] = (prev["spec"] + tail)[:300]
                                used = True
                                continue
                        continue
                    rest = cs[idx_i + 1:]
                    # 期望: 位号|名称|规格|数量|材质|温度|压力|介质|变更  (9 项左右)
                    def g(i, d=""):
                        return rest[i] if i < len(rest) else d
                    rows.append({
                        "no": cs[idx_i],
                        "tag": g(0),
                        "name": g(1),
                        "spec": g(2),
                        "qty": g(3),
                        "mat": g(4),
                        "temp": g(5),
                        "pres": g(6),
                        "media": g(7),
                        "chg": g(8),
                        "group": cur_group,
                    })
                    prev = rows[-1]
                    used = True
            if used:
                pages_used.append(pi + 1)
    data = [r for r in rows if "no" in r]
    return {"rows": rows, "data": data, "pages": pages_used,
            "groups": [r["group"] for r in rows if "group" in r and "no" not in r],
            "warn": warn}


def to_orig8(res: dict) -> list:
    """转真稿 8 列口径: 序号|设备名称|规格|材质|数量/台|操作条件|内部物料|备注

    ⚠ 分组行判定必须用 `"no" not in r` —— 数据行也带 `group` 字段(归属用),
      用 `"group" in r` 会把所有数据行误判成分组行。
    """
    out = []
    for r in res["rows"]:
        if "no" not in r:
            out.append(["", r["group"], "", "", "", "", "", ""])
            continue
        temp = r.get("temp", "")
        pres = r.get("pres", "")
        op = ""
        if temp and pres:
            op = f"温度{temp}；压力{pres}"
        elif temp:
            op = f"温度{temp}"
        elif pres:
            op = f"压力{pres}"
        out.append([r.get("no", ""), r.get("name", ""), r.get("spec", ""),
                    r.get("mat", ""), r.get("qty", ""), op,
                    r.get("media", ""), r.get("chg", "")])
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--first", type=int, required=True)
    ap.add_argument("--last", type=int, required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = extract(a.pdf, a.first, a.last)
    print(f"页 {len(r['pages'])} / 总行 {len(r['rows'])} / 数据行 {len(r['data'])} / 分组 {len(r['groups'])}",
          file=sys.stderr)
    for g in r["groups"]:
        print("  分组:", g, file=sys.stderr)
    if a.json:
        print(json.dumps(to_orig8(r), ensure_ascii=False, indent=1))
    else:
        for row in to_orig8(r)[:8]:
            print(row)
