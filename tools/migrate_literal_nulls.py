"""迁移: 字面 None 清洗 + 建构筑物数据重提取 (全库, 2026-10 复查发现)

背景:
  c8c7ff0a4d (长兴链路验证) 的 buildings 混入字面 "None" (早期提取残留) 与
  建筑卫生学检查表误行 (name="1".."6", area=检查情况长句) → 正文 (1.1/3.1.1/3.1.6)、
  表3.7-1 单元格、备注多处泄漏 "None"。
  材料 04_原有项目_现状评价报告.docx 内有正确建构筑物表 (21 行), 现行提取逻辑
  (report_parser 同款列定位) 可干净提取 → 以材料为源重提取。

修法:
  A. 重提取 (仅当 buildings 含字面 None / 误行时): 从该项目材料目录的现状评价 docx
     重提取 → 替换 buildings; 并删除 built_tables['建构筑物表'] 旧骨架
     (骨架优先规则: 删后导出按修正数据现算重建)。
  B. 全项目字面 None 清扫: section_states 文本 / built_tables 单元格 / 关键数据
     字段 → 空串 (缺数据留白, 不写伪值)。行 name 清空后为空 → 删行。
幂等: 重跑 0 命中。
用法: .venv/bin/python tools/migrate_literal_nulls.py [--apply]
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "ohs.db"

_NONE_RE = re.compile(r"(?<![A-Za-z])None(?![A-Za-z])")
DATA_LIST_FIELDS = ("buildings", "equipment", "products", "materials", "ppe",
                    "facilities", "public_works", "staffing", "equipment_detail",
                    "detections", "health_check", "hazard_grid", "emergency_supplies")


def _clean_str(s: str) -> str:
    return _NONE_RE.sub("", s)


def _extract_buildings_from_material(pid: str) -> list[dict] | None:
    """从材料目录的 现状评价 docx 重提取建构筑物表 (report_parser 同款列定位)"""
    mdir = ROOT / "data" / "materials" / pid
    if not mdir.is_dir():
        return None
    cands = [p for p in mdir.rglob("*.docx") if "现状评价" in p.name]
    if not cands:
        return None
    try:
        import docx
        from docx.oxml.ns import qn
        from docx.table import Table
    except Exception:
        return None
    out: list[dict] = []
    for f in cands:
        try:
            doc = docx.Document(str(f))
        except Exception:
            continue
        for ch in doc.element.body.iterchildren():
            if ch.tag != qn("w:tbl"):
                continue
            tb = Table(ch, doc)
            rows = [[c.text.replace("\n", " ").strip() for c in r.cells] for r in tb.rows]
            if not rows:
                continue
            flat = " | ".join(str(c) for r in rows for c in r)
            if "建构筑" not in flat and not ("建筑面积" in flat and "占地" in flat):
                continue
            head = rows[0]
            def _bc(kws):
                for i, h in enumerate(head):
                    for kw in kws:
                        if kw in h:
                            return i
                return -1
            c_name = _bc(["建构筑物名称"])
            if c_name < 0:
                continue  # 非建构筑物表 (如卫生检查表) → 跳过
            c_fun = _bc(["功能区", "区域"])
            c_fire = _bc(["火灾危险", "火灾危险性"])
            c_arch = _bc(["耐火等级", "耐火"])
            c_floor = _bc(["层数"])
            c_area = _bc(["占地面积", "占地"])
            c_barea = _bc(["建筑面积", "建面"])
            for row in rows[1:]:
                name = row[c_name] if (c_name >= 0 and c_name < len(row)) else (row[0] if row else "")
                if not name or name == "建构筑物名称":
                    continue
                b = {"name": name}
                for key, ci in (("功能区", c_fun), ("火灾危险类别", c_fire), ("耐火等级", c_arch),
                                ("floors", c_floor), ("area", c_area), ("floor_area", c_barea)):
                    if ci >= 0 and ci < len(row):
                        b[key] = row[ci]
                if b["name"] not in [x.get("name") for x in out]:
                    out.append(b)
    return out or None


def main() -> int:
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    n_re = n_clean = n_rebuild = 0

    for r in conn.execute("SELECT id, data FROM project").fetchall():
        pid, raw = r["id"], r["data"]
        try:
            d = json.loads(raw)
        except Exception:
            continue
        changed = False

        # ---- A. buildings 重提取 (含字面 None 时) ----
        bld = d.get("buildings") or []
        bld_txt = json.dumps(bld, ensure_ascii=False)
        if "None" in bld_txt:
            new_bld = _extract_buildings_from_material(pid)
            if new_bld and len(new_bld) >= 10:
                clean_cnt = sum(1 for x in new_bld if "None" not in json.dumps(x, ensure_ascii=False))
                print(f"  [A] {pid}: buildings 重提取 {len(bld)} → {len(new_bld)} 行 "
                      f"(材料源, 干净 {clean_cnt})")
                d["buildings"] = new_bld
                bt = d.get("built_tables")
                if isinstance(bt, dict) and "建构筑物表" in bt:
                    del bt["建构筑物表"]
                    print(f"      built_tables['建构筑物表'] 旧骨架已删 (导出重算)")
                n_rebuild += 1
                changed = True
            else:
                print(f"  [A] {pid}: buildings 含 None 但材料重提取失败/行数过少 → 走 B 清扫")

        # ---- B. 全项目字面 None 清扫 ----
        hits = 0
        # B1. section_states 文本
        for sn, st in (d.get("section_states") or {}).items():
            t = (st or {}).get("text") or ""
            if isinstance(t, str) and _NONE_RE.search(t):
                nt = _clean_str(t)
                hits += len(_NONE_RE.findall(t))
                if apply:
                    st["text"] = nt
        # B2. built_tables 单元格 (就地重建)
        def clean_bt(o):
            nonlocal hits
            if isinstance(o, str):
                if _NONE_RE.search(o):
                    hits += len(_NONE_RE.findall(o))
                    return _clean_str(o)
                return o
            if isinstance(o, list):
                return [clean_bt(x) for x in o]
            if isinstance(o, dict):
                return {k: clean_bt(v) for k, v in o.items()}
            return o
        bt = d.get("built_tables")
        if bt:
            bt2 = clean_bt(bt)
            if apply:
                d["built_tables"] = bt2
        # B3. 关键数据列表字段
        def clean_list(lst):
            nonlocal hits
            out = []
            for x in lst:
                if isinstance(x, str):
                    if _NONE_RE.search(x):
                        hits += len(_NONE_RE.findall(x))
                        x = _clean_str(x)
                    out.append(x)
                elif isinstance(x, dict):
                    nx = {}
                    drop = False
                    for k, v in x.items():
                        if isinstance(v, str) and _NONE_RE.search(v):
                            hits += len(_NONE_RE.findall(v))
                            v = _clean_str(v)
                        nx[k] = v
                    if "name" in nx and not str(nx.get("name") or "").strip():
                        drop = True
                    if not drop or "name" not in nx:
                        out.append(nx)
                else:
                    out.append(x)
            return out
        for f in DATA_LIST_FIELDS:
            v = d.get(f)
            if isinstance(v, list):
                v2 = clean_list(v)
                if apply:
                    d[f] = v2
        if hits:
            print(f"  [B] {pid}: 字面 None {hits} 处 → 空串/删行")
            n_clean += hits
            changed = True

        if changed and apply:
            conn.execute("UPDATE project SET data=? WHERE id=?",
                         (json.dumps(d, ensure_ascii=False), pid))

    if apply:
        conn.commit()
    conn.close()
    print(f"\n{'✅ 已落库' if apply else '[DRY-RUN]'} 重提取 {n_rebuild} 项目; 清扫 {n_clean} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
