"""诊断: 实际提交的 unit 与预期差在哪 (给 ex.submit 打桩)

起因: 完整生成报 135/135 done, 但 section_states 只有 126 个键,
      缺 11.2-11.7 / 12 / 12.1 / 3.2.2 —— 用代码复现 unit 列表却是 135 个。
      → 必须看**运行时**真实提交的列表, 不能靠复现猜。
"""
import sys

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

import web.app as A
from web.report_struct import CHAPTERS, SUBS, SUBS3, SUBS4, _extract_product_units  # noqa

PID = "c8c7ff0a4d"

SUBMITTED = []
_orig_gen = A._gen_one


def _spy_gen(pid, sec, sub, title=None, cache=None):
    SUBMITTED.append((sec, sub))
    return _orig_gen(pid, sec, sub, title, cache)


A._gen_one = _spy_gen

from web.projects_db import get_project, update_section_state  # noqa


def main():
    p = get_project(PID)
    data = dict(p["data"])
    data["section_states"] = {}
    A._save_project_data(PID, data)
    A._cache.pop(f"assess:{PID}", None)
    print("开始打桩生成…", flush=True)
    A._run_generate_all(PID, "spy_" + str(int(__import__("time").time())))
    keys = {(sb or s) for s, sb in SUBMITTED}
    print(f"\n提交 {len(SUBMITTED)} 个单元, 去重键 {len(keys)}")
    exp = []
    for ch in CHAPTERS:
        exp.append(ch)
        for sn, _t in SUBS.get(ch, []):
            exp.append(sn)
            for sub3, (par, _t3) in SUBS3.items():
                if par == sn:
                    exp.append(sub3)
                    for num, _t4 in SUBS4.get(sub3, []):
                        exp.append(num)
    exp += [s4 for s4, _ in _extract_product_units(data)]
    missing = [k for k in exp if k not in keys]
    print(f"预期 {len(exp)} | 未提交: {missing}")
    ss = (get_project(PID)["data"].get("section_states") or {})
    print(f"存储键 {len(ss)} | 存储缺失: {sorted(set(exp) - set(ss))}")


if __name__ == "__main__":
    main()
