"""物质单元批量 LLM 深文 — 每物质的完整毒理学段 (后台)"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, ".")
from web.projects_db import get_project  # noqa: E402


def factors_of(pid: str) -> list[str]:
    """从项目数据取危害因素"""
    from knowledge.oel import connect
    from knowledge.project_assess import assess_project
    p = get_project(pid)
    conn = connect()
    r = assess_project(conn, {"name": p["name"], "industry": p["data"].get("industry", ""),
                              "equipment": p["data"].get("equipment", []),
                              "detections": p["data"].get("detections", []),
                              "process_text": p["data"].get("process_text", "")})
    conn.close()
    return [h["factor"] for h in r.get("hazards", [])]


def main(pid: str):
    from web.unit_gen import material_deep
    factors = factors_of(pid)
    print(f"{pid}: {len(factors)} 个物质因子", flush=True)
    outs = {}
    for f in factors:
        try:
            t = material_deep(f, {}, {})
            outs[f] = t
            print(f"  {f}: {len(t)}字 ✓", flush=True)
        except Exception as e:
            print(f"  {f}: ERR {str(e)[:40]}", flush=True)
    # 保存到项目 materials dir
    import json as _json
    from web.uploads import project_dir
    pd = project_dir(pid)
    (pd / "A2i_物质毒理学.json").write_text(
        _json.dumps(outs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"MAT_DONE {pid} factors={len(factors)}", flush=True)


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "demo-cx"
    main(pid)
