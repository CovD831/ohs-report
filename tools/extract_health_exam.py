#!/usr/bin/env python3
"""从 GBZ 188-2025《职业健康监护技术规范》原文提取「体检项目与周期」表

目标表 (照真稿 6 列):
  危害因素 | 体检类别 | 岗前职业健康检查 | 岗中职业健康检查 | 健康检查周期 | 离岗职业健康检查

数据源: acquire/raw/nhc_gbz/pdfs/GBZ_188_2025.pdf (本地, 139 页, 官方原文)
红线: 只提取原文有的, 提取不到留空标注 —— 绝不编造

用法:
  .venv/bin/python tools/extract_health_exam.py            # 提取并打印统计
  .venv/bin/python tools/extract_health_exam.py --dump 7.1 # dump 单节原始行
  .venv/bin/python tools/extract_health_exam.py --write    # 写入 data/health_exam.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "acquire" / "raw" / "nhc_gbz" / "pdfs" / "GBZ_188_2025.pdf"
OUT = ROOT / "data" / "health_exam.json"

# 分部章: 5 化学 / 6 粉尘 / 7 物理 / 8 生物 / 9 特殊作业
CHAP_PREFIX = {
    "5": "化学因素",
    "6": "粉尘",
    "7": "物理因素",
    "8": "生物因素",
    "9": "特殊作业",
}

# 页眉页脚噪声: 独立数字行 / "GBZ 188－2025"
_NOISE = re.compile(r"^(?:\d{1,3}|GBZ\s*188\s*[－\-—]?\s*2025)$")


def load_lines() -> list[str]:
    import pdfplumber

    with pdfplumber.open(str(PDF)) as pdf:
        raw = "\n".join((p.extract_text() or "") for p in pdf.pages)
    out = []
    for ln in raw.split("\n"):
        s = ln.strip()
        if not s or _NOISE.match(s):
            continue
        out.append(s)
    return out


# ── 节标题: "7.1 噪声" / "5.13 可溶性钡化合物"  (x.y, 不是 x.y.z)
# ⚠ 上限必须够宽: 6.1 "游离二氧化硅粉尘[结晶型二氧化硅粉尘，又称矽尘（游离二氧化硅含量≥10%的无机性粉尘)]"
#   正文长达 44 字, 早期写成 .{0,40} → 该节整节丢失（6.1/6.4/6.6 全丢）。
# ⚠ 必须排除目录行: TOC 形如 "6.7 金属及其化合物粉尘（…）.....70", 靠 "…" / ".." 点线识别,
#   否则目录版与正文版同名同号 → 后者被前者污染（曾致 6.7 丢失检查内容）。
_SEC = re.compile(r"^(\d{1,2}\.\d{1,2})\s+(\S.{0,60})$")
_TOC = re.compile(r"[.·]{4,}\s*\d{1,3}$")  # 点线+页码 = 目录行


def split_sections(lines: list[str]) -> list[tuple[str, str, list[str]]]:
    """→ [(编号, 标题, 行列表)] 只保留 5/6/7/8/9 章的二级因素节"""
    secs: list[tuple[str, str, list[str]]] = []
    cur = None
    for ln in lines:
        if _TOC.search(ln):          # 目录行不入节, 也不进 body
            continue
        m = _SEC.match(ln)
        if m:
            num, title = m.group(1), m.group(2).strip()
            ch = num.split(".")[0]
            # 因素节必须属于 5-9 章, 且标题不含句号结尾(排除正文行)
            if ch in CHAP_PREFIX and not title.endswith("。"):
                if cur:
                    secs.append(cur)
                cur = (num, title, [])
                continue
        if cur is not None:
            cur[2].append(ln)
    if cur:
        secs.append(cur)
    return secs


# ── 检查内容提取: 定位 "必检项目"/"选检项目"/"补充检查项目" 的值 ──────────
def _extract_items(block: list[str]) -> list[str]:
    """从一段行里抽 必检/选检/补充检查 项目名, 返回合并串

    ⚠ 原文两种写法要认: "必检项目：xxx" 与 "必检项目为xxx" (同行无冒号)。
    """
    out: list[str] = []
    for ln in block:
        for tag in ("必检项目", "选检项目", "补充检查项目"):
            m = re.search(tag + r"[：:]\s*(.*)$", ln)
            if not m:
                m = re.search(tag + r"为\s*(.*)$", ln)
            if m:
                v = m.group(1).rstrip("；;。")
                if v:
                    out.append(f"{tag}：{v}")
    return out


def _extract_physical(block: list[str]) -> list[str]:
    """抽 b) 体格检查 下的项目名 (内科常规检查 / 耳科检查 ...)

    真稿口径: 「检查」格 = 体格检查 + 实验室必检 (如噪声的"内科常规检查、耳科检查、
    纯音听阈测试...")。只取实验室必检会漏体格检查 → 必须一并抽。

    ⚠ b) 体格检查 也可能是 "同7.1.1.2b)" (跨节引用, 噪声在岗即此形态) → 返回哨兵
      "__SAME__" 由调用方继承岗前, 见 _extract_physical_resolved()。
    """
    out: list[str] = []
    grab = False
    for ln in block:
        if re.match(r"^b\)\s*体格检查", ln):
            if re.search(r"同\s*\S*\d\.\d+\.\d+", ln):
                return ["__SAME__"]
            grab = True
            continue
        if grab:
            if re.match(r"^[cd]\)", ln):  # 到 c) 实验室 为止
                break
            m = re.match(r"^\d+\)\s*(.+)", ln)
            if m:
                out.append(m.group(1).strip().rstrip("；;。"))
    return out


def _clean_exam(phys: list[str], vals: list[str], prev_phys: list[str] | None = None) -> str:
    """合并 体格检查 + 必检/选检 → 真稿风格

    真稿: "必检：内科常规检查、耳科检查、纯音听阈测试、心电图...；选检：声导抗..."
    即 体格检查项目 归入「必检」一并列出。

    phys 含哨兵 "__SAME__" (体格检查=同前节) 时用 prev_phys (通常是岗前的体格检查) 继承。
    """
    if "__SAME__" in phys:
        phys = list(prev_phys or [])
    be = xa = ""
    for v in vals:
        if v.startswith("必检项目"):
            be = v.split("：", 1)[1]
        elif v.startswith(("选检项目", "补充检查项目")):
            xa = v.split("：", 1)[1]
    parts = []
    if phys:
        parts.append(f"必检：{'、'.join(phys)}" + (f"、{be}" if be else ""))
    elif be:
        parts.append(f"必检：{be}")
    if xa:
        parts.append(f"选检：{xa}")
    return "；".join(parts)


def _resolve_same(block: list[str], own: str, pre_text: str) -> str:
    """处理 "检查内容：同x.y.z" / "检查内容同x.y.z" 的继承

    高温 7.3.2.2 = "检查内容：同7.3.1.2" → 在岗检查内容 = 岗前检查内容 (真稿如此)。
    """
    if own:
        return own
    joined = " ".join(block)
    if re.search(r"同\s*\S*\.\d+\.\d+", joined):
        return pre_text or "同岗前"
    return own


def parse_section(num: str, title: str, body: list[str]) -> dict:
    """→ {factor, category, pre, on, cycle, off, source}

    ⚠ 小节号不是固定的 1/2/3: 有节没有「在岗期间」而直接是「应急健康检查」(如 5.8 氧化锌),
    因此**必须按小节标题语义**判定角色, 不能假设 x.1=岗前 x.2=岗中 x.3=离岗。
    """
    # 按标题语义索引小节: 上岗前 / 在岗期间 / 离岗时
    role_idx: dict[str, int] = {}
    sec_starts: list[int] = []
    for i, ln in enumerate(body):
        m = re.match(rf"^{re.escape(num)}\.(\d+)\s+(\S+)", ln)
        if m:
            sec_starts.append(i)
            t = m.group(2)
            if "上岗前" in t:
                role_idx.setdefault("pre", i)
            elif "在岗" in t:
                role_idx.setdefault("on", i)
            elif "离岗" in t:
                role_idx.setdefault("off", i)

    def slice_of(role: str) -> list[str]:
        if role not in role_idx:
            return []
        s = role_idx[role]
        nxt = next((v for v in sorted(sec_starts) if v > s), len(body))
        return body[s:nxt]

    pre_phys = _extract_physical(slice_of("pre"))
    pre = _clean_exam(pre_phys, _extract_items(slice_of("pre")))
    on = _clean_exam(
        _extract_physical(slice_of("on")), _extract_items(slice_of("on")), prev_phys=pre_phys
    )
    # 在岗检查内容写作 "同x.1.2" → 继承岗前 (高温 7.3 即此形态)
    on = _resolve_same(slice_of("on"), on, pre)
    # 离岗若无独立检查内容, 原文常写 "同x.2.2" → 继承岗中
    off_block = slice_of("off")
    off = _clean_exam(
        _extract_physical(off_block), _extract_items(off_block), prev_phys=_extract_physical(slice_of("on")) or pre_phys
    )
    if not off and off_block:
        joined = " ".join(off_block)
        if re.search(r"同\s*\S*\d\.\d\.\d", joined):
            off = "同岗中"

    # 周期: 在「在岗期间」段里找 "健康检查周期" (可能多行 a) b))
    cyc = ""
    blk = slice_of("on")
    for i, ln in enumerate(blk):
        if "健康检查周期" in ln:
            head = re.split(r"健康检查周期[：:]?", ln, 1)[1].strip()
            rest = [x for x in blk[i + 1 :] if re.match(r"^[a-z]\)", x)]
            parts = ([head] if head else []) + [r.lstrip("abc) ").strip() for r in rest]
            cyc = "；".join(p for p in parts if p).rstrip("；;。")
            break
    # 离岗节里也可能给周期(少数)
    if not cyc:
        for ln in slice_of("off"):
            if "健康检查周期" in ln:
                cyc = re.split(r"健康检查周期[：:]?", ln, 1)[1].strip().rstrip("；;。")
                break

    ch = num.split(".")[0]
    return {
        "factor": title,
        "code": num,
        "category": CHAP_PREFIX.get(ch, ""),
        "pre": pre,
        "on": on,
        "cycle": cyc,
        "off": off or ("同岗中" if on else ""),
        "source": f"GBZ 188—2025 {num}",
    }


def main() -> None:
    lines = load_lines()
    secs = split_sections(lines)
    if "--dump" in sys.argv:
        want = sys.argv[sys.argv.index("--dump") + 1]
        for num, title, body in secs:
            if num == want:
                print(f"=== {num} {title} ({len(body)} 行) ===")
                for ln in body[:70]:
                    print("  ", ln)
                return
        print(f"未找到节 {want}; 可用: {[s[0] for s in secs][:20]}")
        return

    rows = [parse_section(n, t, b) for n, t, b in secs]
    ok = [r for r in rows if r["pre"] or r["on"]]
    print(f"节总数: {len(rows)}  有检查内容: {len(ok)}")
    from collections import Counter

    print("分部:", dict(Counter(r["category"] for r in rows)))
    print("\n=== 样例 (前 6 条有内容的) ===")
    for r in ok[:6]:
        print(f"\n【{r['code']} {r['factor']}】({r['category']})")
        print("   岗前:", r["pre"][:90] or "—")
        print("   岗中:", r["on"][:90] or "—")
        print("   周期:", r["cycle"][:70] or "—")
        print("   离岗:", r["off"][:60] or "—")

    missing = [r["code"] for r in rows if not (r["pre"] or r["on"])]
    if missing:
        print(f"\n⚠ 无检查内容的节 ({len(missing)}): {missing}")

    if "--write" in sys.argv:
        OUT.write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"\n✅ 写入 {OUT} ({len(rows)} 条)")


if __name__ == "__main__":
    main()
