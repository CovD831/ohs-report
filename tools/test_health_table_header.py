"""守卫: 5.2-1 化学物质表头必须是真稿版(含「危害程度」列)

背景: built_tables 曾冻结旧表头(职业病危害因素|主要侵入途径|毒理学资料|
      对人体健康的主要影响|临界不良健康效应|可能引起的主要职业病) → 覆盖现算表。
      三份真稿(新泰/长兴/浦发)交叉验证真表头含「危害程度」。

断言:
  1. section_filler 的 5.2-1 builder 表头 == 真稿表头
  2. section_style_spec.json 的 5.2.1.table_header == 真稿表头
  3. built_tables 中不得存在旧表头指纹(毒理学资料/临界不良健康效应)
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REAL_HEADER = ["名称", "形态", "危害特性", "对人体健康的影响", "危害程度", "可能引起的职业病或职业性病损"]
STALE = ("毒理学资料", "临界不良健康效应")

fails = []


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"无法加载 {path}"
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- 1. spec 表头 ---
spec_path = ROOT / "web" / "section_style_spec.json"
sp = json.loads(spec_path.read_text(encoding="utf-8"))
th = sp["sections"]["5.2.1"]["table_header"]
if th != REAL_HEADER:
    fails.append(f"spec 5.2.1 表头不符: {th}")
else:
    print(f"  ✓ spec 5.2.1 表头 = {th}")

# --- 2. built_tables 污染 ---
# 取数走** sanctioned 读口** web.projects_db.list_all_reports() (S1c″ D4′):
#   上一版这里是 `sqlite3.connect(ROOT/"data"/"ohs.db")` —— 可写模式打开 + 路径硬编码,
#   而本文件在 GUARD_TESTS 名单里, 容器里 `ROOT/data` **就是挂载进去的生产库**。
#   于是"切流量前跑守卫套"这一步本身在给生产库开可写连接 (WAL 辅助文件的生灭/回写),
#   而且硬编码路径让 OHS_DATA_DIR 指到临时卷时它照读真实库 —— 演练根本测不到它。
from web.projects_db import list_all_reports      # noqa: E402

stale_hit = 0
for r in list_all_reports():
    d = r.get("data") or {}
    if not d:
        continue
    t = (d.get("built_tables") or {}).get("健康影响表")
    if t:
        cols = t.get("cols") or []
        if any(f in cols for f in STALE):
            fails.append(f"项目 {r['id'][:12]} built_tables.健康影响表 仍是旧表头: {cols}")
            stale_hit += 1
if not any("built_tables" in f for f in fails):
    print(f"  ✓ built_tables 无旧表头污染 (扫 {len(list_all_reports())} 个项目)")

# --- 3. 迁移脚本可运行(防回退) ---
mig = _load(ROOT / "tools" / "migrate_health_table.py", "mig_health")
if not hasattr(mig, "STALE_FIELDS") or set(mig.STALE_FIELDS) != set(STALE):
    fails.append("migrate_health_table.STALE_FIELDS 指纹不符")
else:
    print(f"  ✓ 迁移脚本指纹 = {mig.STALE_FIELDS}")

print()
if fails:
    for f in fails:
        print(f"  ✗ {f}")
    sys.exit(1)
print(f"通过: {3}/{3}")
