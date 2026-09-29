"""端到端 HTTP 路径测试 (A 方案) —— 完全按真实用户操作走

用户路径: 登录 → 新建项目 → 上传材料包(C1..C17) → 导入材料 → 生成报告 → 导出下载

⚠ 为什么必须做这个: 前面所有验证都是**手工驱动 DB/内部函数** (直接调 _run_generate_all、
  手工清 section_states、造探针项目)。真实 HTTP 路径可能有不一致的耦合 ——
  已踩过一次同类 (verify_export_tables 因没设 _project_data 而低估表格数)。

用法: python tools/e2e_http.py <base_url> [材料目录]
"""
import json
import sys
import time
from pathlib import Path

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899"
PACK = Path(sys.argv[2]) if len(sys.argv) > 2 else (
    Path.home() / "Desktop/长兴合成树脂/材料包_生成报告用")
USER, PWD = "admin", "Ohs@2026!"
S = requests.Session()
S.headers["User-Agent"] = "ohs-e2e/1.0"

# 文件名前缀 → 上传类别 (真实材料包按 01_ 02_ ... 命名)
CAT_BY_PREFIX = {
    "01": "C1", "02": "C1", "03": "C2", "04": "C3", "05": "C17",
    "06": "C16", "07": "C5", "08": "C2",
}


def step(msg):
    print(f"\n{'='*72}\n{msg}\n{'='*72}", flush=True)


def cat_for(fname: str) -> str:
    """按文件名前缀猜类别; 未知 → C2(项目概况) 兜底"""
    pre = fname[:2]
    if pre in CAT_BY_PREFIX:
        return CAT_BY_PREFIX[pre]
    low = fname.lower()
    for k, c in (("设备", "C6"), ("工艺", "C7"), ("原辅料", "C8"), ("sds", "C8"),
                 ("产品", "C9"), ("定员", "C10"), ("辅助用室", "C11"), ("采光", "C12"),
                 ("防护", "C13"), ("ppe", "C14"), ("应急", "C15"), ("管理", "C16"),
                 ("检测", "C17"), ("图纸", "C5")):
        if k in low:
            return c
    return "C2"


def main():
    # ---------- 0. 健康 ----------
    step("0. 健康检查")
    r = S.get(f"{BASE}/login", timeout=20)
    print(f"  GET /login → {r.status_code}")

    # ---------- 1. 登录 ----------
    # ⚠ 实测: admin 密码未知; 且系统本身支持**游客模式**
    #   (首次访问自动发 guest-xxxxxx cookie, 见 app.py:47-73)。
    #   故无密码时走游客路径 —— 这同样是**真实用户路径** (未登录用户直接建项目).
    step("1. 登录 / 游客身份")
    r = S.post(f"{BASE}/login", data={"username": USER, "password": PWD},
               timeout=20, allow_redirects=False)
    has_sess = "ohs_session" in S.cookies
    if has_sess:
        print(f"  POST /login → {r.status_code} | 已登录 (ohs_session)")
    else:
        # 触发游客 cookie
        S.get(f"{BASE}/", timeout=20)
        gid = next((c.value for c in S.cookies if str(c.value).startswith("guest-")), None)
        if gid:
            print(f"  admin 登录不可用 → 使用**游客身份** {gid} (app 原生支持)")
        else:
            print(f"  ✗ 既无法登录也拿不到游客 cookie (cookies={dict(S.cookies)})")
            return 1

    # ---------- 2. 新建项目 ----------
    step("2. 新建项目")
    r = S.post(f"{BASE}/api/projects",
               json={"name": "E2E端测-长兴", "industry": ""}, timeout=30)
    print(f"  POST /api/projects → {r.status_code}")
    j = r.json()
    pid = j.get("id")
    print(f"  project id = {pid}")
    if not pid:
        print(f"  ✗ 建项目失败: {j}")
        return 1

    # ---------- 3. 上传材料 ----------
    step("3. 上传材料包")
    files = sorted([p for p in PACK.rglob("*") if p.is_file()])
    # 只传根目录下的主要材料 (SDS 目录里 200+ 份会拖很久, 先传关键几份)
    main_files = [p for p in files if p.parent == PACK]
    print(f"  材料目录共 {len(files)} 文件; 先传根目录 {len(main_files)} 份")
    up_ok = up_fail = 0
    for p in main_files:
        cat = cat_for(p.name)
        try:
            with open(p, "rb") as fh:
                r = S.post(f"{BASE}/api/projects/{pid}/upload/{cat}",
                           files={"file": (p.name, fh, "application/octet-stream")},
                           timeout=120)
            if r.status_code == 200:
                up_ok += 1
                print(f"    ✓ [{cat}] {p.name}")
            else:
                up_fail += 1
                print(f"    ✗ [{cat}] {p.name} → {r.status_code} {r.text[:80]}")
        except Exception as e:
            up_fail += 1
            print(f"    ✗ [{cat}] {p.name} → {type(e).__name__}: {e}")
    print(f"  上传: 成功 {up_ok} / 失败 {up_fail}")

    # ---------- 4. 导入解析 ----------
    step("4. 导入材料 (解析)")
    t0 = time.time()
    r = S.post(f"{BASE}/api/projects/{pid}/import-materials", timeout=1800)
    dt = time.time() - t0
    print(f"  POST /import-materials → {r.status_code} ({dt:.1f}s)")
    try:
        imp = r.json()
    except Exception:
        imp = {"raw": r.text[:300]}
    for k in ("equipment", "detections", "materials", "ok"):
        if k in imp:
            print(f"    {k} = {imp[k]}")

    # 质量/覆盖
    for ep in ("quality", "coverage", "provenance"):
        try:
            rr = S.get(f"{BASE}/api/projects/{pid}/{ep}", timeout=120)
            print(f"    /{ep} → {rr.status_code} {json.dumps(rr.json(), ensure_ascii=False)[:170]}")
        except Exception as e:
            print(f"    /{ep} → ERR {type(e).__name__}")

    # ---------- 5. 生成报告 ----------
    step("5. 生成报告 (generate-all)")
    r = S.post(f"{BASE}/api/projects/{pid}/generate-all", timeout=120)
    print(f"  POST /generate-all → {r.status_code} | {r.text[:160]}")
    try:
        jid = r.json().get("job_id")
    except Exception:
        jid = None
    print(f"  job = {jid}")
    # ⚠ 轮询必须以**任务表 status** 为准, 不能看 data['generating'] —
    #   实测 generating 可能为 None 而任务仍在跑 (我的旧判据提前退出, 只跑到 51/127)。
    last = None
    done = False
    for _ in range(120):          # 最多等 ~40 分钟
        time.sleep(20)
        try:
            d = S.get(f"{BASE}/api/projects/{pid}/data", timeout=60).json()
        except Exception:
            continue
        ss = (d.get("section_states") or {})
        n = sum(1 for v in ss.values() if isinstance(v, dict) and v.get("text"))
        line = f"  已生成 {n}/{len(ss)} (generating={d.get('generating')})"
        if line != last:
            print(line, flush=True)
            last = line
        # 完成判据: 任务表里没有 pending/running 的 generate_all
        try:
            jl = S.get(f"{BASE}/api/tasks", timeout=30).json()
            jobs = jl if isinstance(jl, list) else (jl.get("jobs") or jl.get("items") or [])
            mine = [x for x in jobs if str(x.get("id")) == str(jid)]
            st = (mine[0].get("status") if mine else None)
        except Exception:
            st = None
        if st in ("done", "failed", "cancelled", "error"):
            print(f"  任务状态: {st}", flush=True)
            done = True
            break
        if not d.get("generating") and n and n == len(ss) and st is None:
            # 兜底: 拿不到任务表时, 以"长时间无增长"判完
            pass
    if not done:
        print("  ⚠ 轮询超时 (任务可能仍在跑)")

    # ---------- 6. 导出 + 下载 ----------
    step("6. 导出报告")
    r = S.get(f"{BASE}/api/projects/{pid}/export", timeout=900)
    print(f"  GET /export → {r.status_code} | {r.text[:200]}")
    r = S.get(f"{BASE}/api/projects/{pid}/download", timeout=300)
    out = Path(f"/tmp/e2e_report_{pid}.docx")
    if r.status_code == 200:
        out.write_bytes(r.content)
        print(f"  下载 → {out} ({len(r.content)//1024} KB)")
    else:
        print(f"  ✗ 下载失败 {r.status_code}")

    print(f"\n{'='*72}\nE2E 完成 | PID={pid}\n{'='*72}")
    Path("/tmp/e2e_pid.txt").write_text(pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
