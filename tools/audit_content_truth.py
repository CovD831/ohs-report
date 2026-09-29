"""内容正确性抽查: 抽报告关键数据点, 与"材料源头"逐条对照

不看字数/密度 (那是代理指标), 只看**事实对不对**:
  ① 接触限值 (PC-TWA) — 应来自 GBZ 2.1 标准库, 不是 LLM 记忆
  ② 检测浓度 (CTWA)  — 应来自检测报告的原始数值
  ③ 风险类别/行业     — 应来自 risk_category 库 + 项目数据
  ④ 定员人数          — 应来自定员表
  ⑤ 游离二氧化硅含量   — 应来自检测报告 (视觉提取捡回的那个)

每条给出: 报告值 / 源头值 / 判定。
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")

PID = "c8c7ff0a4d"
PACK = Path.home() / "Desktop/长兴合成树脂/材料包_生成报告用"


def main():
    c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
    c.row_factory = sqlite3.Row
    d = json.loads(c.execute("SELECT data FROM project WHERE id=?", (PID,)).fetchone()["data"])
    bt = d.get("built_tables") or {}

    print("=" * 88)
    print("① 接触限值 (报告表 vs 标准库 oel_limit)  —— 检查是否 LLM 编的")
    print("=" * 88)
    lim = bt.get("接触限值表") or {}
    rows = lim.get("rows") or []
    cols = lim.get("cols") or []
    print(f"表列: {cols}")
    ok = bad = na = 0
    checked = []
    for r in rows[:14]:
        name = str(r[0]).strip()
        # 表结构: 种类 | 限值1..4 | 备注 ; 取非— 的数字列
        vals = [str(x).strip() for x in r[1:5] if str(x).strip() not in ("—", "")]
        db = c.execute(
            "SELECT factor_name, oel_type, value, unit, source_standard "
            "FROM oel_limit WHERE factor_name=? OR factor_name LIKE ?",
            (name, f"%{name}%")).fetchall()
        dbv = {(x["oel_type"], str(x["value"])) for x in db}
        if not vals:
            na += 1
            checked.append((name, "—", "无值", "无需核（未制定限值）"))
            continue
        real = {v for _, v in dbv}
        hit = [v for v in vals if v in real]
        if hit:
            ok += 1
            checked.append((name, "/".join(vals), "/".join(sorted(real)) or "—", "✓ 与标准库一致"))
        else:
            bad += 1
            checked.append((name, "/".join(vals),
                            "/".join(sorted(real)) or "库中无",
                            "⚠ 不一致"))
    for n, rv, db, verdict in checked:
        print(f"  {n:22} 报告={rv:14} 库={db:18} {verdict}")
    print(f"\n  一致 {ok} | 不一致 {bad} | 未制定限值 {na}")

    print()
    print("=" * 88)
    print("② 检测浓度 (报告 detections vs 原始检测报告)")
    print("=" * 88)
    dets = d.get("detections") or []
    for x in dets[:10]:
        src = x.get("source") or "text"
        pg = x.get("_page")
        print(f"  {str(x.get('factor')):22} ctwa={str(x.get('ctwa')):10} "
              f"sio2={str(x.get('sio2') or '-'):8} 来源={src}"
              + (f" p{pg}" if pg else ""))

    print()
    print("=" * 88)
    print("③ 风险类别 / 行业 (报告 vs risk_category 库)")
    print("=" * 88)
    ind = d.get("industry")
    print(f"  项目 industry = {ind}")
    m = re.search(r"([A-Z]?\d{2,4})", str(ind or ""))
    if m:
        code = m.group(1)
        code3 = re.sub(r"\D", "", code)[:3]
        for cand in (code, "C" + code3, "D" + code3[:2]):
            r = c.execute("SELECT * FROM risk_category WHERE industry_code LIKE ?",
                          (f"%{cand}%",)).fetchall()
            if r:
                for x in r[:3]:
                    print(f"  库: {dict(x)}")
                break
        else:
            print("  ⚠ 库中未匹配到")

    print()
    print("=" * 88)
    print("④ 定员 (报告 vs 原始定员表)")
    print("=" * 88)
    st = d.get("staffing") or []
    tot = sum(int(x.get("count") or 0) for x in st if str(x.get("count") or "").isdigit())
    print(f"  材料定员表: {len(st)} 条岗位, 合计 {tot} 人")
    bk = bt.get("班制定员表") or {}
    print(f"  报告班制定员表: {len(bk.get('rows') or [])} 行")

    print()
    print("=" * 88)
    print("⑤ 游离二氧化硅 (视觉提取捡回的字段, 回溯原检测报告)")
    print("=" * 88)
    sio = [x for x in dets if x.get("sio2")]
    for x in sio:
        print(f"  报告值 sio2={x.get('sio2')} @ {x.get('sampling_point') or x.get('factor')}")
        print(f"  来源文件: {x.get('_file')} p{x.get('_page')} (视觉提取)")
    if not sio:
        print("  (无)")


if __name__ == "__main__":
    main()
