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


def refresh(pid: str, mdir: str, apply: bool = False) -> int:
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
        print("  ✓ 无差异, 无需刷新")
        return 0
    print(f"\n共 {len(changed)} 处差异")

    if not apply:
        print("\n[dry-run] 未写入。加 --apply 落库。")
        return 0

    # 备份
    bak = Path(f"/tmp/ohs.db.bak.{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(DB, bak)
    print(f"\n已备份 → {bak}")

    # 只替换 detections, 其余整块保留
    new_data = deepcopy(data)
    new_data["detections"] = fresh
    c.execute("UPDATE project SET data=?, updated=? WHERE id=?",
              (json.dumps(new_data, ensure_ascii=False), time.time(), pid))
    c.commit()
    keys_before, keys_after = set(data), set(new_data)
    assert keys_before == keys_after, "字段集合被改变! 中止"
    print(f"✓ 已刷新 detections ({len(old_dets)} → {len(fresh)} 条), 其余 {len(keys_after)-1} 个字段未动")
    return 0


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    pid, mdir = sys.argv[1], sys.argv[2]
    apply = "--apply" in sys.argv
    return refresh(pid, mdir, apply)


if __name__ == "__main__":
    sys.exit(main())
