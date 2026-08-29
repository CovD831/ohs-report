#!/usr/bin/env python3
"""
报告 BenchMark 统一入口 — 确定性 + 非确定性 完整评测
====================================================
用法:
  python tools/report_bench_run.py --docx path/to/report.docx \
      [--pid <项目id>] [--db data/ohs.db] [--reference path/to/原报告.docx] \
      [--project-json path/to/project_data.json]

输出: 评测卡 (确定性分数 + LLM Judge 分数 + 问题清单)
"""
from __future__ import annotations
import argparse, json, subprocess, sys, os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description="报告完整评测 (确定性 + 非确定性)")
    ap.add_argument("--docx", required=True, help="报告 docx")
    ap.add_argument("--pid", help="项目 id")
    ap.add_argument("--db", default="data/ohs.db")
    ap.add_argument("--reference", help="参考报告 (原报告) docx")
    ap.add_argument("--project-json", help="项目 data JSON")
    ap.add_argument("--skip-judge", action="store_true", help="跳过 LLM Judge")
    args = ap.parse_args()

    cards = {}

    # 1) 确定性层
    cmd = [sys.executable, str(HERE / "report_bench.py"), "--docx", args.docx]
    if args.pid:
        cmd += ["--pid", args.pid, "--db", args.db]
    if args.project_json:
        cmd += ["--project-json", args.project_json]
    if args.reference:
        cmd += ["--reference", args.reference]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        cards["deterministic"] = json.loads(r.stdout)
    except Exception:
        cards["deterministic"] = {"error": r.stderr[-500:]}

    # 2) 非确定性层
    if not args.skip_judge:
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            # 从 Hermes .env 读 (不硬编码)
            envf = Path.home() / ".hermes" / ".env"
            if envf.exists():
                for ln in envf.read_text().splitlines():
                    if ln.startswith("DEEPSEEK_API_KEY="):
                        key = ln.split("=", 1)[1].strip()
        if key:
            r2 = subprocess.run(
                [sys.executable, str(HERE / "report_judge.py"), "--docx", args.docx],
                capture_output=True, text=True,
                env={**os.environ, "DEEPSEEK_API_KEY": key})
            try:
                cards["judge"] = json.loads(r2.stdout)
            except Exception:
                cards["judge"] = {"error": r2.stderr[-500:]}

    # 汇总评测卡
    det = cards.get("deterministic", {})
    judge = cards.get("judge", {})
    summary = {
        "报告": args.docx,
        "确定性分数": det.get("total_deterministic", "N/A"),
        "确定性明细": det.get("scores", {}),
        "LLM Judge 分数": judge.get("total_judge", "N/A"),
        "LLM Judge 明细": judge.get("scores", {}),
        "关键问题": [],
    }
    for dim, items in det.items():
        if isinstance(items, list):
            for it in items:
                summary["关键问题"].append(f"[确定性][{it.get('severity')}] {it.get('note','')[:120]}")
    for c in judge.get("key_concerns", []):
        summary["关键问题"].append(f"[Judge] {c[:120]}")
    cards["summary"] = summary

    print(json.dumps(cards, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
