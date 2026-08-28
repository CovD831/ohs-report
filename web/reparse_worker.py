"""阶段0 材料解析子进程 — 与 uvicorn worker 隔离

背景: 复合报告 PDF 解析峰值 ~800MB, 1.87G 小内存服务器上若在 worker 进程内解析,
全局 OOM-kill 会杀死整个 worker → 生成任务永远 pending 0% (core dump/无 error)。

方案: 本进程独立运行 import_materials_from_dir, 先 setrlimit 限地址空间;
超出限制抛 MemoryError(子进程内可捕获, 返回空 dict), 不会触发内核 OOM-kill 影响主进程。
主进程(subprocess 调用方)只依赖退出码和 stdout JSON; 子进程被杀也不影响 worker。

用法: python -u reparse_worker.py <pid>
输出: stdout 一行 JSON (提取字段), 失败/受限时 {}
"""
import sys
import os
import json
from pathlib import Path

MEM_LIMIT_MB = int(os.environ.get("PARSE_MEM_LIMIT_MB", "1024"))  # 地址空间上限

def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else ""
    if not pid:
        print("{}", flush=True)
        return
    # 先限内存再 import (web.app 模块加载本身 ~50MB)
    try:
        import resource
        lim = MEM_LIMIT_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    except Exception:
        pass

    # 项目根: 服务器容器是 /app, 本地开发按脚本位置推导
    root = "/app" if os.path.isdir("/app") else str(Path(__file__).resolve().parent.parent)
    sys.path.insert(0, root)
    try:
        from web.app import import_materials_from_dir
        d = import_materials_from_dir(pid)
    except MemoryError:
        d = {}          # 内存受限: 调用方沿用旧数据
    except Exception:
        d = {}
    # 仅输出可 JSON 序列化的字段
    out = {}
    for k, v in d.items():
        if isinstance(v, (list, dict, str, int, float, bool)) or v is None:
            out[k] = v
    json.dump(out, sys.stdout, ensure_ascii=False)
    print("", flush=True)

if __name__ == "__main__":
    main()
