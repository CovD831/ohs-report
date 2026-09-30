"""Q3 在真实新泰缓存上的效果验证 (路径可移植: 本地 repo 或容器 /app 均可跑;
样本缓存服务器上没有 → SKIP 优雅退出)"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from web.factor_clean import clean_factor_name  # noqa: E402

CACHE = ROOT / "data" / "vision_cache" / "a66517be268181e6.json"
if not CACHE.exists():
    print(f"SKIP: 样本缓存不存在 ({CACHE}) — 服务器无 vision_cache 属预期")
    sys.exit(0)
vd = json.loads(CACHE.read_text())
dets = vd["detections"]

fixed = 0
flagged = 0
samples = []
for x in dets:
    raw = str(x.get("factor") or "").strip()
    if not raw:
        continue
    out, warns = clean_factor_name(raw)
    if out != raw:
        fixed += 1
        if len(samples) < 10:
            samples.append((raw, out, warns[0] if warns else ""))
    if warns:
        flagged += 1

print(f"新泰 detections {len(dets)} 条")
print(f"  名字被修正: {fixed} 条")
print(f"  带警告(需人工核对): {flagged} 条")
print()
print("=== 修正样本 ===")
for r, o, w in samples:
    print(f"  {r[:52]}")
    print(f"    → {o[:52]}")
    print(f"      {w[:70]}")
print()

# 关键: 残缺名的处理
print("=== 残缺名 (应只标记不改) ===")
n = 0
for x in dets:
    raw = str(x.get("factor") or "").strip()
    if not raw:
        continue
    out, warns = clean_factor_name(raw)
    if any("待人工核对" in w for w in warns):
        print(f"  {raw!r}")
        print(f"    → {out!r}  (未改内容, 仅归一括号)")
        n += 1
        if n >= 4:
            break
