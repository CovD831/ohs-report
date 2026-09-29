"""验证 5g/5h 约束效果: 重生"逐条念表"最严重的单元, 对比前后

对照组: 约束前生成的 section_states (已存库) vs 现在重新生成
指标: 字数 / 罗列句式出现次数 / 数字密度
"""
import json
import re
import sqlite3
import sys

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

PID = "c8c7ff0a4d"
# 逐条念表的典型单元
TARGETS = ["5.4.1", "4.2.1", "4.6.6"]

# 违规句式: "<因素>接触水平…低于/符合…限值"
PAT = re.compile(r"接触水平[^。；;]{0,40}(?:低于|符合|未超过)[^。；;]{0,40}(?:限值|PC-TWA|mg/m)")


def stats(txt: str) -> dict:
    n = max(len(txt), 1)
    return {
        "字符": len(txt),
        "念表句": len(PAT.findall(txt)),
        "数字密度‰": round(len(re.findall(r"\d+(?:\.\d+)?", txt)) * 1000 / n, 1),
    }


def main():
    c = sqlite3.connect(f"/Users/abaaba/Projects/ohs-report/data/ohs.db")
    c.row_factory = sqlite3.Row
    d = json.loads(c.execute("SELECT data FROM project WHERE id=?", (PID,)).fetchone()["data"])
    old_ss = d.get("section_states") or {}

    from web.llm_draft import draft_detail, draft_sub

    print(f"{'单元':10}{'旧字':>7}{'新字':>7}{'旧念表句':>10}{'新念表句':>10}{'旧密度':>8}{'新密度':>8}")
    print("-" * 72)
    for k in TARGETS:
        old = (old_ss.get(k) or {}).get("text") or ""
        if not old:
            print(f"{k:10}  (库里无旧文本, 跳过)")
            continue
        try:
            if k.count(".") >= 2:
                new = draft_detail(PID, k, "", None)
            else:
                new = draft_sub(PID, k.split(".")[0], k)
        except Exception as e:
            print(f"{k:10}  ERR {type(e).__name__}: {e}")
            continue
        so, sn = stats(old), stats(new)
        print(f"{k:10}{so['字符']:>7}{sn['字符']:>7}"
              f"{so['念表句']:>10}{sn['念表句']:>10}"
              f"{so['数字密度‰']:>8}{sn['数字密度‰']:>8}")
        print()
        print(f"  [新] {new[:260]}")
        print()


if __name__ == "__main__":
    main()
