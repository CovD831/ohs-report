"""接入设计备忘: 视觉提取接进 import 后的两个待解决问题

问题1 (成本/阻塞): vision_detections 是**同步**的, 125页 ≈17分钟。
  → 导入接口会挂住 17 分钟, 用户体验不可接受, 且每次 re-import 都重跑。
  解决方向 (择一/组合):
    a) 缓存: 按 (文件路径, mtime, 页数) 缓存结果到 data/vision_cache/*.json,
       命中率会很高 (同一份检测报告反复导入)
    b) 异步: 照 external_tables 的模式, 导入先返回, 视觉提取后台跑完 patch detections
    c) 按需: 只在"检测报告是扫描件 且 文本层提取到的因素 < N"时触发

问题2 (重复导入): check_stale_data / refresh_stale_data 都会触发 vision
  → 那些工具要加开关跳过 vision (否则每次校验等 17 分钟)
"""
import hashlib
import json
import time
from pathlib import Path

CACHE = Path("/tmp/vision_cache_demo")
CACHE.mkdir(exist_ok=True)


def cache_key(pdf: Path) -> str:
    """缓存键: 路径+大小+mtime (内容变了 mtime 就变)"""
    st = pdf.stat()
    raw = f"{pdf.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def demo():
    p = Path.home() / "Desktop/长兴合成树脂/材料包_生成报告用/05_类比检测_职业卫生检测2025.pdf"
    k = cache_key(p)
    print(f"文件: {p.name}")
    print(f"缓存键: {k}")
    f = CACHE / f"{k}.json"
    if f.exists():
        d = json.loads(f.read_text())
        print(f"命中缓存 ({len(d.get('detections') or [])} 条)")
    else:
        print("未命中 → 需要跑视觉提取 (此处省略, 仅演示键计算)")
    print()
    print("建议缓存位置: data/vision_cache/<key>.json")
    print("键包含 mtime → 报告更新后自动失效, 不会用旧结果")


if __name__ == "__main__":
    demo()
