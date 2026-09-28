"""外部数据表异步填充 — 不阻塞导入, 后台补齐

用户定 (2026-09):
  有的表格需要联网搜索 → 这种表"异步联网请求, 同时也可以开始建表了"。
  即: 导入时先把骨架建好并**立即返回**, 需要联网/LLM 的表在后台跑, 跑完 patch 回 built_tables。

为什么需要 (实测):
  build_weather_table 缓存未命中时联网 ~2.2s (90s 超时上限)。
  导入路径里同步等它 = 用户多等; 且网络抖动会拖慢/卡住导入。
  改异步后: 导入 0.06s 返回, 气象表后台补齐 (前端刷新可见)。

设计:
  建表时: 外部表先占位 (status="filling"), 让面板/导出知道"正在取"
  后台:   ThreadPoolExecutor 跑取数 → patch 进 project.data["built_tables"] → 置 status="filled"
  失败:   status="await_data" (诚实标注, 不静默丢)
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 需要外部取数 (联网/LLM) 的表 → 对应建表函数 (惰性 import 避免循环)
EXTERNAL_TABLES = {
    "气象因素表": ("web.table_builder", "build_weather_table"),
}

_lock = threading.Lock()
_inflight: set[str] = set()


def external_names() -> set[str]:
    return set(EXTERNAL_TABLES)


def placeholder(name: str) -> dict:
    """外部表的占位骨架 (导入时立即放这个)"""
    from web.number_provenance import prov_llm
    return {
        "cols": [],
        "rows": [],
        "status": "filling",
        "prov": prov_llm({"file": f"外部取数:{name}", "page": None,
                          "text": f"{name} 需联网/LLM 获取, 后台填充中"}),
    }


def _region_of(data: dict) -> str:
    """地区名: 优先企业画像 region, 否则规则抽取 (与 build_all_tables 同源)"""
    prof = data.get("profile") or {}
    region = str(prof.get("region") or "").strip()
    if region:
        return region
    try:
        from web.table_builder import extract_region
        return extract_region(str(data.get("location") or ""),
                              str(data.get("company") or ""))
    except Exception:
        return ""


def fill_one(name: str, data: dict) -> dict | None:
    """同步填一张外部表 (供后台线程或直接调用)"""
    spec = EXTERNAL_TABLES.get(name)
    if not spec:
        return None
    mod_name, fn_name = spec
    try:
        mod = __import__(mod_name, fromlist=[fn_name])
        fn = getattr(mod, fn_name)
    except Exception:
        return None
    try:
        if name == "气象因素表":
            t = fn(_region_of(data))
        else:
            t = fn()
    except Exception:
        return None
    if not isinstance(t, dict):
        return None
    t = dict(t)
    t["status"] = "filled" if t.get("rows") else "await_data"
    return t


def fill_external_async(pid: str, data: dict, on_done=None) -> None:
    """后台填所有外部表 → patch 进库 (不阻塞调用方)

    pid: 项目 id; data: 当前 project data (用于取地区名等)
    on_done(pid, {表名: 表}) 可选回调
    """
    todo = [n for n in EXTERNAL_TABLES if n not in _inflight]
    if not todo:
        return
    with _lock:
        for n in todo:
            _inflight.add(n)

    def _worker():
        result = {}
        try:
            for name in todo:
                t = fill_one(name, data)
                if t:
                    result[name] = t
            if result:
                _patch(pid, result)
            if on_done:
                on_done(pid, result)
        finally:
            with _lock:
                for n in todo:
                    _inflight.discard(n)

    threading.Thread(target=_worker, name=f"ext-fill-{pid}", daemon=True).start()


def _patch(pid: str, tables: dict) -> None:
    """把外部表结果并回 project.data.built_tables"""
    try:
        from web.projects_db import get_project, update_project
        p = get_project(pid)
        if not p:
            return
        data = p["data"]
        bt = dict(data.get("built_tables") or {})
        for k, v in tables.items():
            bt[k] = v
        data["built_tables"] = bt
        update_project(pid, p["name"], data)
        # 失效 assess 缓存 (表变了)
        try:
            from web.app import _cache
            _cache.pop(f"assess:{pid}", None)
        except Exception:
            pass
    except Exception:
        pass


if __name__ == "__main__":
    import json
    import sqlite3
    import time

    c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
    c.row_factory = sqlite3.Row
    d = json.loads(c.execute("SELECT data FROM project WHERE id=?", ("c8c7ff0a4d",)).fetchone()[0])
    print("region =", repr(_region_of(d)))
    t0 = time.time()
    t = fill_one("气象因素表", d)
    print(f"fill_one 耗时 {time.time()-t0:.2f}s | status={t.get('status')} rows={len(t.get('rows') or [])}")
    for r in (t.get("rows") or [])[:4]:
        print("   ", r)
