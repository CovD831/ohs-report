#!/usr/bin/env python3
"""从可研提取「一、项目背景」原文 —— 1.1 项目背景 的数据源

真稿 7.1 项目背景 为多段: 行业/产品定义 + 市场 + 公司动机 + 建设内容 + 设备 + 产能 + 法规委托。
其中「项目由来/动机/建设条件」段直接来自可研正文, 属**权威原文**, 不应由 LLM 概括(用户红线: 材料=权威定义勿概括)。

本脚本抽取可研 1.2 项目概况 下「一、项目背景」整段原文 → data/project_background.json

输出: data/project_background.json
    {pid: {"paras": [段1, 段2, ...], "page": 7}}
"""
import sys, os, re, json, glob
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAT = os.path.join(HERE, "data", "materials")
OUT = os.path.join(HERE, "data", "project_background.json")


def _find_kyan(pid: str):
    pats = [
        os.path.join(MAT, pid, "C2_项目概况", "*可研*.pdf"),
        os.path.join(MAT, pid, "C2_项目概况", "*申请报告*.pdf"),
        os.path.join(MAT, pid, "C2_项目概况", "*.pdf"),
    ]
    for p in pats:
        g = sorted(glob.glob(p))
        if g:
            return g[0]
    return None


def _extract(path: str):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
        # 在 1.2 项目概况 附近找「一、项目背景」
        for i in range(0, min(n, 80)):
            t = pdf.pages[i].extract_text() or ""
            if "项目背景" in t and "项目概况" in t:
                # 拼本页+下一页, 取「一、项目背景」→「二、建设地点」之间
                buf = t + "\n" + (pdf.pages[i + 1].extract_text() or "")
                m1 = buf.find("一、项目背景")
                if m1 < 0:
                    m1 = buf.find("项目背景")
                m2 = buf.find("二、建设地点")
                if m2 < 0:
                    m2 = buf.find("二、 建设地点")
                if m1 >= 0 and m2 > m1:
                    raw = buf[m1 + len("一、项目背景"):m2]
                    return _clean(raw), i + 1
    return None, None


def _clean(raw: str):
    """去页码行/去空格内嵌/按段切分。"""
    lines = []
    for ln in raw.split("\n"):
        s = ln.strip()
        if not s:
            continue
        # 纯页码行 (如 "3" / "4")
        if re.fullmatch(r"\d{1,3}", s):
            continue
        lines.append(s)
    # 合并为连续文本(可研 PDF 每行硬折行 → 拼接)
    txt = "".join(lines)
    # 去行内多余空格
    txt = re.sub(r"\s+", "", txt)
    # 按「。另一方面」「。此外」「。同时」等保留原句结构 → 这里只做整段, 不硬切
    paras = [p.strip() for p in re.split(r"(?<=。)(?=[随着公同另此])", txt) if p.strip()]
    if len(paras) <= 1:
        paras = [txt] if txt else []
    return paras


def main():
    write = "--write" in sys.argv
    out = {}
    for d in sorted(glob.glob(os.path.join(MAT, "*"))):
        pid = os.path.basename(d)
        if not os.path.isdir(d):
            continue
        k = _find_kyan(pid)
        if not k:
            print(f"{pid}: 跳过(无可研)")
            continue
        paras, pg = _extract(k)
        if not paras:
            print(f"{pid}: 跳过(未找到『一、项目背景』)")
            continue
        out[pid] = {"paras": paras, "page": pg}
        print(f"{pid}: {len(paras)} 段, {sum(len(p) for p in paras)} 字 (p{pg})")
    if write:
        old = {}
        if os.path.exists(OUT):
            old = json.load(open(OUT, encoding="utf-8"))
        old.update(out)
        json.dump(old, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("已写", OUT)


if __name__ == "__main__":
    main()
