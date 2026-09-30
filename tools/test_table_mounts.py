"""回归测试: 表挂载点必须可达 + 骨架表必须在导出里出现

防的是这类静默失败:
  _SUB_TABLE_MAP 把表挂到一个**结构里不存在的节号** → 导出 walk 永远不到
  → 整张表消失, 且没有任何报错 (2026-09: 工艺检查表挂 3.5.3, 而 3.5 无子节)

用法: python tools/test_table_mounts.py
退出码 0 = 通过
"""
import sys
from pathlib import Path

ROOT = next((p for p in (Path("/app"), Path(__file__).resolve().parent.parent)
             if (p / "web" / "app.py").exists()), Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))


def reachable_mounts() -> tuple[list[tuple[str, list[str]]], set[str]]:
    from tools.check_mount_points import all_sections
    from web.word_export import _SUB_TABLE_MAP
    valid = all_sections()
    bad = []
    for sub, m in sorted(_SUB_TABLE_MAP.items()):
        if not m:
            continue
        if sub not in valid:
            bad.append((sub, m[1]))
    return bad, valid


def skeleton_names() -> set[str]:
    from web.word_export import _SUB_TABLE_MAP
    out = set()
    for sub, m in sorted(_SUB_TABLE_MAP.items()):
        if m:
            out.update(m[1])
    return out


def test_mounts_reachable() -> int:
    """挂载点必须在报告结构里存在 (允许已知例外, 但必须显式登记)"""
    # 已知例外: 5.1.1.2 由产品单元展开而来, 非 SUBS 字面节号 → 可接受
    ALLOW = {"5.1.1.2"}
    bad, valid = reachable_mounts()
    hard = [(s, n) for s, n in bad if s not in ALLOW]
    print(f"挂载点检查: 结构 {len(valid)} 节, 不可达 {len(bad)} 个 "
          f"(允许例外 {len(bad) - len(hard)})")
    for s, names in bad:
        tag = "允许" if s in ALLOW else "★错误"
        print(f"  [{tag}] {s} -> {names}")
    if hard:
        print(f"  ✗ {len(hard)} 个挂载点不可达且未登记例外")
        return len(hard)
    print("  ✓ 通过")
    return 0


def test_orig_map_hygiene() -> int:
    """ORIG 编号/表题映射卫生 (防章号平移后死键/槽位不平行 → 表题静默回退内部名)

    2026-09 实例: 挂载 3.5.3→3.5 / 5.4.3→10.1 / 10.2→11.2 迁移后映射留旧键,
    表题静默回退内部名 (「本项目工艺检查及评价」→「工艺检查表」)。
    规则: ① 键必须仍是挂载节 (无死键); ② 有映射的节编号/表题列表平行,
    且与 wanted 名单位次一一对应 (2.1.1 应急三拆登记例外)。
    """
    from web.orig_table_map import ORIG_CAPTIONS, ORIG_TABLE_NOS
    from web.word_export import _SUB_TABLE_MAP

    mounts = {k for k, v in _SUB_TABLE_MAP.items() if v}
    split_ok = {"2.1.1"}  # wanted 1 → 编号/表题 3 (应急药品/物资/洗眼器 三拆)
    bad = 0
    dead = sorted((set(ORIG_TABLE_NOS) | set(ORIG_CAPTIONS)) - mounts)
    if dead:
        print(f"  ✗ ORIG 映射死键 (挂载已迁走): {dead}")
        bad += len(dead)
    for k in sorted(set(ORIG_TABLE_NOS) | set(ORIG_CAPTIONS)):
        nos = ORIG_TABLE_NOS.get(k) or []
        caps = ORIG_CAPTIONS.get(k) or []
        if len(nos) != len(caps):
            print(f"  ✗ {k}: 编号 {len(nos)} ≠ 表题 {len(caps)} (须平行)")
            bad += 1
            continue
        wanted = (_SUB_TABLE_MAP.get(k) or (None, []))[1]
        if k not in split_ok and wanted and len(wanted) != len(nos):
            print(f"  ✗ {k}: wanted {len(wanted)} ≠ 槽位 {len(nos)} (按名配对失效)")
            bad += 1
    tag = "✗" if bad else "✓"
    print(f"  {tag} ORIG 映射: {len(mounts)} 挂载 / {len(ORIG_TABLE_NOS)} 编号 / {len(ORIG_CAPTIONS)} 表题"
          + (f" — {bad} 项异常" if bad else " — 无死键, 槽位平行"))
    return bad


def test_tables_export(tmpdir=None) -> int:
    """真项目跑导出, 骨架里有行的表必须都在 docx 里出现"""
    import json
    import re
    import sqlite3

    import docx

    db = ROOT / "data" / "ohs.db"
    if not db.exists():
        print("跳过导出检查 (无数据库)")
        return 0
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    # 优先 d4c2b5450c (服务器真项目), 否则挑任一有数据的项目
    row = conn.execute("SELECT id, data FROM project WHERE id=?",
                       ("d4c2b5450c",)).fetchone()
    if not row:
        for r in conn.execute("SELECT id, data FROM project"):
            try:
                dd = json.loads(r["data"])
            except Exception:
                continue
            if dd.get("built_tables") or dd.get("equipment_detail") or dd.get("equipment"):
                row = r
                break
    if not row:
        print("跳过导出检查 (无可用项目)")
        return 0
    pid, data = row["id"], json.loads(row["data"])

    from web.derived_fields import derive_fields
    from web.table_skeleton import build_skeletons
    from web.external_tables import fill_one
    from web.word_export import export_docx

    data.update({k: v for k, v in derive_fields(data).items() if v})
    sk = build_skeletons(data)
    for n in list(sk):
        if sk[n].get("status") == "filling":
            t = fill_one(n, data)
            if t:
                sk[n] = t
    data["built_tables"] = sk
    try:
        from knowledge.project_assess import assess_project
        from knowledge.oel import connect as oc
        assess = assess_project(oc(), data)
    except Exception:
        assess = {}

    out = Path(tmpdir or "/tmp") / "test_mount_export.docx"
    export_docx({"id": pid, "data": data, "name": "t"}, assess, out)
    doc = docx.Document(str(out))
    caps = [p.text.strip() for p in doc.paragraphs
            if re.match(r"^表\s*[\d.]+[-—]\d+", p.text.strip())]
    blob = "\n".join(caps)
    with_rows = {n for n, t in sk.items() if t.get("rows")}

    sys.path.insert(0, str(ROOT / "tools"))
    from verify_export_tables import CAP_HINT
    missing = [n for n in with_rows
               if not any(h in blob for h in CAP_HINT.get(n, [n]))]
    print(f"导出检查: 项目 {pid} | 骨架有行 {len(with_rows)} | docx 表 {len(doc.tables)} "
          f"| 表题 {len(caps)}")
    if missing:
        print(f"  ✗ 骨架有行但导出未见: {missing}")
        return len(missing)
    print(f"  ✓ {len(with_rows)}/{len(with_rows)} 全部导出")
    return 0


if __name__ == "__main__":
    bad = test_mounts_reachable()
    print()
    bad += test_orig_map_hygiene()
    print()
    bad += test_tables_export()
    print()
    print("结果:", "✓ 通过" if bad == 0 else f"✗ 失败 {bad} 项")
    sys.exit(1 if bad else 0)
