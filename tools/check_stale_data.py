"""陈旧数据检测: 存储的 detections vs 当前代码重跑值

背景 (2026-09):
  项目 c8c7ff0a4d 存着 `聚乙烯粉尘 ctwa=8` / `滑石粉尘 ctwa=1`,
  一度被判定为"提取器取错列"bug。**查证后是陈旧数据**:
    - 当前 report_parser 输出 `<0.33` (正确)
    - 存储值是**早期版本**写的, 那时列定位还没修 (注释里的"修复: 表25..."就是那次)
  → 代码没问题, 需要的是**刷新旧项目的提取结果**。

⚠ 教训: 看到"存储数据不对"时, 先跑一遍当前代码看它还复现不复现。
  不复现 = 陈旧数据; 复现 = 真 bug。别直接改代码。

用法:
  python tools/check_stale_data.py <pid> [材料目录]
  python tools/check_stale_data.py --all
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = next((p for p in (Path("/app"), Path(__file__).resolve().parent.parent)
             if (p / "web" / "app.py").exists()), Path.cwd())
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "ohs.db"


def stored_dets(pid: str) -> list:
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
    if not r:
        return []
    return (json.loads(r["data"]).get("detections") or [])


def fresh_dets(material_dir: str) -> list:
    from web.app import import_materials_from_dir
    return (import_materials_from_dir(material_dir) or {}).get("detections") or []


def cmp_one(pid: str, mdir: str) -> bool:
    """返回 True=一致(或无法判断), False=存在陈旧差异"""
    old = stored_dets(pid)
    if not old:
        print(f"  {pid}: 无存储 detections")
        return True
    if not mdir or not Path(mdir).exists():
        print(f"  {pid}: 材料目录缺失, 跳过 ({mdir})")
        return True
    new = fresh_dets(mdir)
    omap = {str(d.get("factor")): str(d.get("ctwa")) for d in old}
    nmap = {str(d.get("factor")): str(d.get("ctwa")) for d in new}
    diff = []
    for f, ov in omap.items():
        nv = nmap.get(f)
        if nv is not None and nv != ov:
            diff.append((f, ov, nv))
    if diff:
        print(f"  ⚠ {pid}: {len(diff)} 处陈旧差异")
        for f, ov, nv in diff[:8]:
            print(f"      {f:22} 存储={ov!r:12} 重跑={nv!r}")
        return False
    print(f"  ✓ {pid}: 一致 ({len(omap)} 条)")
    return True


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] == "--all":
        c = sqlite3.connect(str(DB))
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT id, name FROM project ORDER BY updated DESC").fetchall()
        print(f"扫描 {len(rows)} 个项目 (材料目录需自行对照)\n")
        for r in rows:
            print(f"  {r['id']} {r['name']}")
        print("\n逐个指定材料目录重跑: python tools/check_stale_data.py <pid> <mdir>")
        return 0
    pid = args[0]
    mdir = args[1] if len(args) > 1 else ""
    print(f"检查 {pid} (材料: {mdir or '未指定'})")
    ok = cmp_one(pid, mdir)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
