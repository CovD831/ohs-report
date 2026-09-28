"""正确触发完整生成 (等价于 /api/report/generate) — 含清空 section_states

⚠ 关键: 必须像 app.py:272-277 那样**先清空 section_states** 再生成。
  直接调 _run_generate_all 会保留旧键 (章号平移后会留下孤儿键, 如 v46 把结论
  11→12 后, 旧的 '11'/'11.1' 内容还在, 导出走新结构 12 找不到 → 结论章空白)。
"""
import sys
import time

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

import web.app as A

PID = sys.argv[1] if len(sys.argv) > 1 else "c8c7ff0a4d"


def main():
    conn = A.connect()
    p = A.get_project(PID)
    if not p:
        print(f"项目不存在: {PID}")
        return 1
    data = dict(p["data"])
    # 与 app.py 生成入口一致: 先清空, 否则新旧键混存
    data["section_states"] = {}
    data["generating"] = True
    A._save_project_data(PID, data)
    A._cache.pop(f"assess:{PID}", None)
    print(f"已清空 section_states, 开始生成 pid={PID}", flush=True)
    jid = "regen_" + str(int(time.time()))
    A._run_generate_all(PID, jid)
    # 复查
    p2 = A.get_project(PID)
    ss = (p2["data"].get("section_states") or {})
    txt = {k: len((v or {}).get("text") or "") for k, v in ss.items()}
    print(f"\n生成完成: {len(ss)} 单元, 有正文 {sum(1 for v in txt.values() if v)}")
    # 关键章检查
    for ch in ("10", "11", "12"):
        ks = [k for k in ss if k == ch or k.startswith(ch + ".")]
        tot = sum(txt.get(k, 0) for k in ks)
        print(f"  第{ch}章: {len(ks)} 单元, {tot} 字  {sorted(ks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
