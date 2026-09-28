"""刷新陈旧项目数据 — 只订正 detections, 其余字段原样保留

背景: 库里的 detections 可能是**早期提取代码**写的 (见 check_stale_data.py)。
      代码已修但数据没回填 → 报告里用的是错值。
      实测 c8c7ff0a4d: 滑石粉尘 ctwa='1' / 聚乙烯粉尘 ctwa='8' (实为 <0.33)。

安全设计:
  ① 写入前自动备份 db 到 /tmp
  ② **只改 detections 一个键**, 其余字段整块保留 (deepcopy)
  ③ 默认 dry-run, 加 --apply 才真写
  ④ 逐条打印 旧值→新值, 便于核对

用法:
  python tools/refresh_stale_data.py <pid> <材料目录>            # 预览
  python tools/refresh_stale_data.py <pid> <材料目录> --apply    # 落库
"""
import json
import shutil
import sqlite3
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT = next((p for p in (Path("/app"), Path(__file__).resolve().parent.parent)
             if (p / "web" / "app.py").exists()), Path.cwd())
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "ohs.db"


def refresh(pid: str, mdir: str, apply: bool = False,
            rebuild_skeleton: bool = False) -> int:
    if not DB.exists():
        print(f"db 不存在: {DB}")
        return 1
    if not Path(mdir).exists():
        print(f"材料目录不存在: {mdir}")
        return 1

    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
    if not row:
        print(f"项目不存在: {pid}")
        return 1
    data = json.loads(row["data"])
    old_dets = data.get("detections") or []

    from web.app import import_materials_from_dir
    fresh = (import_materials_from_dir(mdir) or {}).get("detections") or []
    if not fresh:
        print("重跑未产出 detections, 终止 (避免把好数据清空)")
        return 1

    omap = {str(d.get("factor")): d for d in old_dets}
    nmap = {str(d.get("factor")): d for d in fresh}

    print(f"=== {pid} ===")
    print(f"存储 {len(omap)} 条 / 重跑 {len(nmap)} 条\n")
    changed = []
    for f in sorted(set(omap) | set(nmap)):
        o, n = omap.get(f), nmap.get(f)
        ov = str(o.get("ctwa")) if o else "(无)"
        nv = str(n.get("ctwa")) if n else "(消失)"
        if ov != nv:
            changed.append((f, ov, nv))
            print(f"  Δ {f:24} {ov:10} → {nv}")
    if not changed:
        print("  ✓ detections 无差异")
        if not (rebuild_skeleton and apply):
            print("  (如需仅重建表骨架: 加 --with-skeleton --apply)")
            return 0
        print("  → 跳过 detections 替换, 继续重建表骨架")
    print(f"\n共 {len(changed)} 处差异")

    if not apply:
        print("\n[dry-run] 未写入。加 --apply 落库。")
        return 0

    # 备份
    bak = Path(f"/tmp/ohs.db.bak.{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(DB, bak)
    print(f"\n已备份 → {bak}")

    # 只替换 detections (默认) + 可选重建表骨架
    new_data = deepcopy(data)
    new_data["detections"] = fresh
    # 表骨架: 老项目可能没有 built_tables (该逻辑上线前导入的) → 导出会退化到只剩
    # fill_section 现算的表 (实测 19 张 vs 34 张)。这里一并补建。
    if rebuild_skeleton:
        try:
            from web.table_skeleton import build_skeletons
            _sk = build_skeletons(new_data)
            new_data["built_tables"] = _sk
            _filled = sum(1 for v in _sk.values()
                          if isinstance(v, dict) and v.get("rows"))
            print(f"✓ 重建表骨架: {len(_sk)} 张 (有行 {_filled} 张)")
        except Exception as e:
            print(f"⚠ 重建表骨架失败 (跳过): {type(e).__name__}: {e}")
    c.execute("UPDATE project SET data=?, updated=? WHERE id=?",
              (json.dumps(new_data, ensure_ascii=False), time.time(), pid))
    c.commit()
    # 安全校验: 除 detections (+ 显式重建的 built_tables) 外, 不得改动/丢失其它字段
    _allowed = {"detections", "built_tables"} if rebuild_skeleton else {"detections"}
    _lost = set(data) - set(new_data)
    _touched = {k for k in set(data) & set(new_data) if data[k] != new_data[k]}
    assert not _lost, f"字段丢失! {_lost}"
    assert _touched <= _allowed, f"误改字段! {_touched - _allowed}"
    print(f"✓ 已刷新 detections ({len(old_dets)} → {len(fresh)} 条)")
    print(f"  变更字段: {sorted(_touched)} | 其余 {len(set(new_data)) - len(_touched)} 个字段未动")
    return 0


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    pid, mdir = sys.argv[1], sys.argv[2]
    apply = "--apply" in sys.argv
    rebuild = "--with-skeleton" in sys.argv
    return refresh(pid, mdir, apply, rebuild)


if __name__ == "__main__":
    sys.exit(main())
