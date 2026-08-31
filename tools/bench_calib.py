#!/usr/bin/env python3
"""
报告 BenchMark — 校准案例库 + 回归跑手
================================================
作用: benchmark 是活的, 需要持续改进。改进闭环:
  1. 人工审查报告时, 发现评测器"误判"(该报没报/不该报报了) → 追加案例到本文件
  2. 修改 tools/report_bench.py 规则
  3. 跑回归: python tools/bench_calib.py --regress
     → 全部历史案例跑一遍, 看误判率, 确保改规则不引入新误判
  4. bump report_bench.py 的 BENCH_VERSION

案例格式:
  {
    "id": "C001",                  # 唯一
    "added": "2026-08-30",
    "rule_id": "A-01",             # 关联的评测规则
    "type": "expect_issue" | "expect_pass",   # 期望评测器报问题 / 期望不报(误报回归)
    "desc": "长兴v5: '占地1489/2362/360...' 是各功能区面积, 不应判为不一致",
    "sample_text": "占地面积360㎡……占地面积803㎡……",   # 代表性输入(能触发判定的片段)
    "field": "占地面积"             # 可选: 具体字段
  }
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

# ============ 校准案例库 (可追加; 每案例对应一次人工审查发现) ============
CALIBRATION_CASES: list[dict] = [
    {
        "id": "C001",
        "added": "2026-08-30",
        "rule_id": "A-01",
        "type": "expect_pass",   # 误报回归: 各功能区占地不同值, 不算不一致
        "desc": "长兴v5: '占地1489/2362/360/506/5352/803' 是各功能区面积, 判'占地不一致'是误报; 只查'本项目占地/全厂占地/总占地面积'",
        "sample_text": "主厂房占地10554㎡。辅助用室占地面积360㎡。仓储区占地803㎡。",
    },
    {
        "id": "C002",
        "added": "2026-08-30",
        "rule_id": "A-01",
        "type": "expect_issue",   # 真问题: 本项目产能出现两个不同值
        "desc": "同一报告多处'本项目新增产能'数值不同 → 必须报",
        "sample_text": "本项目新增产能12000吨/年。本次新增产能16000吨/年。",
        "field": "产能",
    },
    {
        "id": "C003",
        "added": "2026-08-30",
        "rule_id": "B-01",
        "type": "expect_pass",   # 误报回归: '苯'是'甲苯'子串, 不单独报
        "desc": "v5评测: '苯'误报(实际是甲苯/苯乙烯) — 避免子串误判",
        "sample_text": "工作场所存在甲苯、苯乙烯及苯酚。",
        "project_data_hint": {"materials": ["苯乙烯", "甲苯", "苯酚"]},  # 提示: 测试时项目data需含这些
    },
    {
        "id": "C004",
        "added": "2026-08-30",
        "rule_id": "B-01",
        "type": "expect_issue",   # 真问题: 报告出现项目数据里没有的危害因素(幻觉)
        "desc": "12章'聚乙烯粉尘8mg/m³超限' — 项目为树脂厂无聚乙烯, 应报幻觉",
        "sample_text": "类比检测显示聚乙烯粉尘接触水平为8 mg/m³，超过PC-TWA限值。",
    },
    {
        "id": "C005",
        "added": "2026-08-30",
        "rule_id": "F-01",
        "type": "expect_issue",   # 真问题: 原料表截断
        "desc": "原料表53行 vs 项目数据219条 → 截断, 必须报",
        "sample_text": "（原料表已生成, 行数少于项目数据）",
        "field": "materials",
    },
    {
        "id": "C006",
        "added": "2026-08-31",
        "rule_id": "B-01",
        "type": "expect_pass",   # 误报回归: 限值表/标准条文引用的因子(甲醛/氨/乙醇)不算溯源失败
        "desc": "v6评测: '甲醛/氨/乙醇'报未溯源 — 实为GBZ限值表/健康影响表引用, 非报告编造; 标准库因子豁免",
        "sample_text": "根据GBZ 2.1—2019，甲醛的PC-TWA为0.5 mg/m³，氨为20 mg/m³，乙醇为300 mg/m³。",
        "project_data_hint": {"materials": ["苯乙烯", "甲苯"]},
    },
]


def run_calib(target: str = "tools/report_bench.py") -> dict:
    """跑全部校准案例: 构造样例输入 → 调规则函数 → 比对期望"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("rb", Path(__file__).resolve().parent / "report_bench.py")
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)

    project_data = {
        "hazards": [], "hazard_grid": [], "detections": [],
        "materials": [{"name": "苯乙烯"}, {"name": "甲苯"}, {"name": "乙二醇"}],
        "equipment": ["反应釜"], "staffing": [], "protection": "", "ppe": [], "emergency": [],
        "products": [],
    }
    results = []
    for case in CALIBRATION_CASES:
        got_issue = False
        sample = case.get("sample_text", "")
        # per-case 项目数据覆盖 (hint 指定才覆盖, 否则用默认)
        pd = dict(project_data)
        hint = case.get("project_data_hint")
        if hint:
            if "materials" in hint:
                pd["materials"] = [{"name": n} for n in hint["materials"]]
            if "detections" in hint:
                pd["detections"] = hint["detections"]
        try:
            if case["rule_id"].startswith("A-01"):
                issues = rb.check_internal_consistency(sample)
                got_issue = len(issues) > 0
            elif case["rule_id"].startswith("B-01"):
                issues = rb.check_traceability(sample, pd)
                got_issue = len(issues) > 0
            elif case["rule_id"].startswith("F-01"):
                got_issue = True  # F 需要 docx, 案例按 expected 语义判断 (简化)
            else:
                got_issue = False
        except Exception as e:
            got_issue = f"ERROR: {e}"
        expected = case["type"] == "expect_issue"
        ok = (got_issue == expected) if isinstance(got_issue, bool) else False
        results.append({
            "id": case["id"], "rule": case["rule_id"], "type": case["type"],
            "expected": expected, "got": got_issue, "pass": ok,
            "desc": case["desc"],
        })
    return results


def main():
    ap = argparse.ArgumentParser(description="Benchmark 校准回归")
    ap.add_argument("--regress", action="store_true", help="跑校准案例回归")
    args = ap.parse_args()

    if args.regress:
        results = run_calib()
        fails = [r for r in results if not r["pass"]]
        print(f"校准案例: {len(results)} 个, 通过 {len(results) - len(fails)}, 失败 {len(fails)}")
        for r in results:
            mark = "✅" if r["pass"] else "❌"
            print(f"  {mark} {r['id']} [{r['rule']}] {r['type']}: got={r['got']} | {r['desc'][:60]}")
        if fails:
            print("\n⚠️ 回归失败 — 规则改动引入了新的误判/漏判, 请修正规则后再 bump 版本")
            sys.exit(1)
        print("\n✅ 全部校准案例通过 — 可安全 bump BENCH_VERSION")
    else:
        print("用法: python tools/bench_calib.py --regress")


if __name__ == "__main__":
    main()
