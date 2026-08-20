"""采集层注册器 — 读 sources.yaml, 调度抓取器

用法:
    python3 -m acquire.registry list            # 列出数据源
    python3 -m acquire.registry run std_samr    # 跑单个源
    python3 -m acquire.registry run --all       # 跑所有可用源
"""
import argparse
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def load_sources():
    with open(HERE / "sources.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run(source_id: str):
    sources = {s["id"]: s for s in load_sources()}
    src = sources.get(source_id)
    if not src:
        print(f"未知数据源: {source_id} (可用: {', '.join(sources)})")
        return 1
    if src.get("status") == "todo":
        print(f"[skip] {source_id} 尚未验证, 跳过")
        return 0
    try:
        mod = __import__(f"acquire.fetchers.{source_id}", fromlist=["fetch"])
        print(f"[run ] {source_id}: {src['name']}")
        return 0 if mod.fetch() is not None else 1
    except ImportError:
        print(f"[todo] {source_id}: 抓取器未实现")
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["list", "run"])
    ap.add_argument("target", nargs="?", default=None)
    args = ap.parse_args()
    if args.action == "list":
        for s in load_sources():
            print(f"{s['id']:16s} [{s['status']:8s}] {s['name']}")
        return 0
    if args.action == "run":
        if args.target == "--all":
            return max(run(s["id"]) for s in load_sources())
        return run(args.target)
    return 1


if __name__ == "__main__":
    sys.exit(main())
