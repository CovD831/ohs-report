"""回归测试: 新鲜导入的检测数据链 — 三条实测 bug 的守卫 (2026-09)

背景 (v3 新鲜导入验证发现, 全在"提取→持久化→消费"链上):
  ① 空骨架覆盖现算表: 导入时无 assess → 评估驱动骨架恒为空 (rows=[]);
     导出 `_tables_for_sub` 照单全换 → 17 张 computed>0 的表被抹平
     (含 h14 物理因素检测结果表 → 整张表从 docx 消失)。
     → 修复: 空骨架不得覆盖现算行 (word_export._tables_for_sub)。

  ② 视觉合并按因素名塌缩: 合并键 = factor ⇒ 61 条噪声采样点 → 1 条;
     真实报告检测表按 (因素, 采样点) 逐行 (表25/表28)。
     → 修复: 合并键 = (归一因素名, 归一采样点), 同点择优保留 (app.py)。

  ③ 同名表冲突: det_by_type 按原始 category 分桶再映射 dtype ⇒
     产出两张同名「检测结果表(化学毒物)」; 导出侧按"节+表名"去重 → 15 行静默丢。
     → 修复: 先归一为 dtype 再分桶 (section_filler.py, 两处)。

判据 (不跑 LLM, 视觉缓存命中):
  - 噪声行 ≥ 15 (逐点; 修复前=1)
  - cstel 非空 ≥ 30 (h11 峰值浓度链)
  - _physical_det_table 非 None 且有行 (h14)
  - _tables_for_sub('4.4.2') 里「物理因素检测结果表」**有行** (① 的直接守卫)
  - 检测结果表(化学毒物) 只有一张 (③ 的直接守卫)

用法: .venv/bin/python tools/test_fresh_import.py
退出码 0 = 通过
"""
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = next((p for p in (Path("/app"), Path(__file__).resolve().parent.parent)
             if (p / "web" / "app.py").exists()), Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
import os
os.chdir(ROOT)

PACK = Path.home() / "Desktop/长兴合成树脂/材料包_生成报告用"
SRC_CACHE = ROOT / "data" / "vision_cache" / "32e58e26097c8e58.json"


def main() -> int:
    if not PACK.exists():
        print(f"SKIP: 材料包不存在 {PACK}")
        return 0
    if not SRC_CACHE.exists():
        print(f"SKIP: 视觉缓存不存在 {SRC_CACHE}")
        return 0

    from web.projects_db import create_project
    created = create_project("__test_fresh_import", "", owner_id="test-regression")
    pid = (created or {}).get("id") if isinstance(created, dict) else created
    if not pid:
        print(f"✗ create_project 未返回 id: {created!r}")
        return 1
    print(f"test project: {pid}")

    from web.uploads import project_dir, save_upload
    # 只传关键文件: 04 (文本层) + 05 (扫描件走视觉缓存)
    for name, cat in (("04_原有项目_现状评价报告.docx", "C3"),
                      ("05_类比检测_职业卫生检测2025.pdf", "C17")):
        f = PACK / name
        if not f.exists():
            print(f"SKIP: 缺材料 {f}")
            return 0
        save_upload(pid, cat, name, f.read_bytes())
    # 种视觉缓存 (键含路径+size+mtime → 复制后按新键写)
    from web.vision_extract import _cache_path
    pdf = next(project_dir(pid).rglob("05_*"))
    cp = _cache_path(pdf)
    shutil.copy(SRC_CACHE, cp)
    print(f"vision cache seeded: {cp.name}")

    from web.app import import_materials_from_dir
    t0 = time.time()
    data = import_materials_from_dir(pid)
    dets = data.get("detections") or []
    print(f"import: {time.time() - t0:.0f}s, detections={len(dets)}")

    from collections import Counter
    fc = Counter(str(x.get("factor") or "") for x in dets)
    noise = fc.get("噪声", 0)
    cstel = sum(1 for x in dets if str(x.get("cstel") or "").strip())

    # ② 逐点合并守卫
    ok_noise = noise >= 15
    print(f"  {'✓' if ok_noise else '✗'} 噪声逐点行 = {noise} (期望 ≥15; 修复前=1)")
    # h11 峰值浓度链
    ok_cstel = cstel >= 30
    print(f"  {'✓' if ok_cstel else '✗'} cstel 非空 = {cstel} (期望 ≥30)")

    # 持久化 (模拟 api_import_materials 的过滤 + 合并)
    from web.projects_db import get_project, update_project
    p = get_project(pid)
    merged = dict(p["data"])
    merged["detections"] = [d for d in dets
                            if d.get("ctwa") is not None or d.get("sio2") or d.get("results")]
    update_project(pid, p["name"], merged)

    # ① h14 物理表 (空骨架不得覆盖现算)
    from knowledge.oel import connect
    from knowledge.project_assess import assess_project
    from web.section_filler import _physical_det_table
    from web import word_export as WE
    kconn = connect()
    proj = {"name": p["name"], "industry": "", "equipment": merged.get("equipment") or [],
            "detections": merged["detections"], "process_text": merged.get("process_text") or "",
            "materials": merged.get("materials") or [], "staffing": merged.get("staffing") or [],
            "hazard_grid": merged.get("hazard_grid") or [],
            "built_tables": merged.get("built_tables") or {}, "profile": merged.get("profile") or {}}
    assess = assess_project(kconn, proj)
    assess["_project_data"] = dict(proj)

    pt = _physical_det_table(kconn, merged["detections"])
    ok_pt = pt is not None and len(pt.get("rows") or []) >= 5
    print(f"  {'✓' if ok_pt else '✗'} _physical_det_table rows = "
          f"{len(pt['rows']) if pt else 0} (期望 ≥5)")

    WE._USED_TABLES.clear()
    ts = WE._tables_for_sub(kconn, "4", "4.4.2", assess)
    pt_mount = next((t for t in ts if t["name"] == "物理因素检测结果表"), None)
    ok_mount = bool(pt_mount and (pt_mount.get("rows") or []))
    print(f"  {'✓' if ok_mount else '✗'} 4.4.2 挂载「物理因素检测结果表」有行 = "
          f"{len((pt_mount or {}).get('rows') or [])} (期望 ≥5; 修复前整表消失)")
    chem_names = [t["name"] for t in ts if "检测结果表" in t["name"]]
    dup_chem = len(chem_names) != len(set(chem_names))
    ok_dup = not dup_chem
    print(f"  {'✓' if ok_dup else '✗'} 检测结果表名唯一 = {chem_names} (③ 同名冲突守卫)")

    # ④ 单一合并点守卫 (同型第6次事故): 解析结果的每个非空键都必须能落库
    from web.app import merge_import_into_data
    _probe = {"name": "年产12000吨X项目", "industry": "C265", "field_provenance": {"company": {"v": 1}},
              "materials": [{"name": "甲醇"}], "material_count": 45, "equipment": ["反应釜"],
              "detections": [{"factor": "噪声"}], "process_text": "x", "empty_thing": ""}
    _merged = merge_import_into_data({}, _probe)
    _must = all([_merged.get("name") == "年产12000吨X项目", _merged.get("industry") == "C265",
                 isinstance(_merged.get("field_provenance"), dict), _merged.get("materials"),
                 "material_count" not in _merged, "equipment" not in _merged,
                 "detections" not in _merged, "process_text" not in _merged,
                 "empty_thing" not in _merged])
    ok_merge = bool(_must)
    print(f"  {'✓' if ok_merge else '✗'} 单一合并点: name/industry/field_provenance 可落库, "
          f"计数与直赋键不串 (④ 白名单漂移守卫)")

    # ⑤ 可研解析: name 不得吞后续标签行 (换行压平负向前瞻守卫)
    PACK3 = PACK / "03_项目申请报告_可研.pdf"
    ok_name = True
    if PACK3.exists():
        from web.report_parser import parse_report_file as _prf
        _rp = _prf(PACK3)
        _nm = str(_rp.get("name") or "")
        ok_name = bool(_nm) and "建设单位" not in _nm and "建设地点" not in _nm and len(_nm) <= 40
        print(f"  {'✓' if ok_name else '✗'} 可研 name 干净 = {_nm[:48]!r} (⑤ 吞行守卫)")
    else:
        print("  - 可研不在材料包, 跳过 ⑤")

    # 清理测试项目 (防污染项目列表)
    try:
        from web.projects_db import _conn as _pc
        c = _pc()
        c.execute("DELETE FROM project WHERE id=?", (pid,))
        c.commit()
        c.close()
    except Exception:
        pass

    all_ok = ok_noise and ok_cstel and ok_pt and ok_mount and ok_dup and ok_merge and ok_name
    print()
    print(f"新鲜导入数据链: {'✓ 通过' if all_ok else '✗ 失败'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
