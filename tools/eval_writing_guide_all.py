"""约束效果总评: 重生全部单元, 对比 念表句/字数 总量

⚠ 会跑满 LLM (135 单元), 慎用。用于验收 5g/5h 的总效果。
"""
import json
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

PID = "c8c7ff0a4d"
PAT = re.compile(r"接触水平[^。；;]{0,40}(?:低于|符合|未超过)[^。；;]{0,40}(?:限值|PC-TWA|mg/m)")


def main():
    c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
    c.row_factory = sqlite3.Row
    d = json.loads(c.execute("SELECT data FROM project WHERE id=?", (PID,)).fetchone()["data"])
    old = {k: (v or {}).get("text") or "" for k, v in (d.get("section_states") or {}).items()}
    old = {k: v for k, v in old.items() if v}

    from web.llm_draft import draft_detail, draft_sub, draft_section

    def regen(k):
        try:
            if k.count(".") >= 2:
                return k, draft_detail(PID, k, "", None)
            if "." in k:
                return k, draft_sub(PID, k.split(".")[0], k)
            return k, draft_section(PID, k)
        except Exception as e:
            return k, f"__ERR__{type(e).__name__}"

    keys = list(old.keys())
    print(f"重生 {len(keys)} 个单元…", flush=True)
    t0 = time.time()
    new = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        for k, t in ex.map(regen, keys):
            if not str(t).startswith("__ERR__"):
                new[k] = t
    print(f"完成 {len(new)} 单元, {time.time()-t0:.0f}s\n")

    def agg(dd):
        chars = sum(len(v) for v in dd.values())
        hits = sum(len(PAT.findall(v)) for v in dd.values())
        nums = sum(len(re.findall(r"\d+(?:\.\d+)?", v)) for v in dd.values())
        return chars, hits, nums

    oc, oh, on = agg(old)
    nc, nh, nn = agg(new)
    print(f"{'':12}{'字符':>10}{'念表句':>10}{'数字密度‰':>12}")
    print("-" * 46)
    print(f"{'约束前':12}{oc:>10}{oh:>10}{on*1000//max(oc,1):>12}")
    print(f"{'约束后':12}{nc:>10}{nh:>10}{nn*1000//max(nc,1):>12}")
    print(f"{'变化':12}{f'{(nc-oc)*100//max(oc,1):+d}%':>10}"
          f"{f'{(nh-oh)*100//max(oh,1):+d}%' if oh else '—':>10}")
    Path = __import__("pathlib").Path
    Path("/tmp/regen_new.json").write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
    print("\n新文本已存 /tmp/regen_new.json")


if __name__ == "__main__":
    main()
