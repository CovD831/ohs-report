#!/usr/bin/env python3
"""从「原有项目现状评价报告」(C3_原有项目) 提取职业卫生管理事实 → data/mgmt_facts.json

背景 (2026-10 P1):
  2.2 职业卫生管理情况 生成稿仅 1185 字 vs 真稿 4602 字 = 26%。
  根因: structure_data.py "2.2" 依赖 project.management 字段, 但 report_parser.py
  只抓第一段匹配 (57 字 "8.1职业健康监护情况……"), C3 现状报告里 11.1—11.12
  的完整管理事实 (机构/制度/检测/告知/培训/标识/应急/申报/档案/经费/既往落实)
  **从未进入管线** → LLM 只能写"公司应…"建议式空话。

原则 (与项目红线一致):
  - **规则提取, 不用 LLM**: 按 C3 章节标题 (11.1—11.12) 切分, 事实段落逐条带溯源
  - 提取不到 → 不写 (下游仍显缺失, 诚实标注)
  - 写入 data/mgmt_facts.json: {pid: {"sections": [{num,title,paras:[...]}], "rules_table": {...}, "src": ...}}

消费: web/structure_data.py get_chapter_info("2.2") 读取 (仿 ohy_invest.json 模式)

用法:
  python tools/extract_mgmt_facts.py <pid>      # 单项目 (默认只预演)
  python tools/extract_mgmt_facts.py --all
  python tools/extract_mgmt_facts.py --all --write
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAT_ROOT = ROOT / "data" / "materials"
OUT_PATH = ROOT / "data" / "mgmt_facts.json"

# C3 现状报告文件名特征
C3_HINT = re.compile(r"现状评价|现状报告")
# 章标题: "11.1职业卫生管理组织机构及人员" / "11职业卫生管理情况调查与评价"
H_MGMT = re.compile(r"^(1[12])(\.\d+)?\s*([\u4e00-\u9fa5“”].{0,40})$")
# 管理章节标题关键词 (用于从正文定位章节边界, 兼容章节号漂移)
# 注意: 两套现状报告用词不同 (ada="管理制度与操作规程", 2863="职业健康监护制度") — 并列收录
MGMT_TITLE_KW = ("管理组织机构", "防治规划", "管理制度与操作", "定期检测", "告知情况",
                 "培训情况", "监护制度", "警示标识", "应急救援预案", "申报情况", "档案管理",
                 "防治经费", "职业卫生经费", "建议落实情况", "管理情况评价")
# 段落质量过滤: 太短/纯图题/表题/页眉水印
_SKIP = re.compile(r"^(图|表)\s*\d|^第\s*\d+\s*页|^\s*$")
# 目录行检测: 结尾带制表符+页码 (如 "11.1  职业卫生管理组织机构及人员 \t 200")
_TOC = re.compile(r"\t\s*\d+\s*$")


def _iter_body(doc):
    """body 顺序遍历 → [('p', text)|('tbl', rows)] ; rows=[[cell,...]]"""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    tbls = {t._element: t for t in doc.tables}
    out = []
    for el in doc.element.body:
        tag = el.tag.split("}")[-1]
        if tag == "p":
            out.append(("p", Paragraph(el, doc).text.strip()))
        elif tag == "tbl":
            tb = tbls.get(el)
            if tb is not None:
                out.append(("tbl", [[c.text.strip() for c in r.cells] for r in tb.rows]))
    return out


def _find_c3(pid_dir: Path) -> Path | None:
    """项目材料目录下找现状评价报告 (C3_原有项目/ 优先, 兜底全目录搜)"""
    c3dir = pid_dir / "C3_原有项目"
    cands = []
    for base in ([c3dir] if c3dir.is_dir() else []) + [pid_dir]:
        for f in sorted(base.rglob("*.docx")):
            if "~$" in f.name:
                continue
            if C3_HINT.search(f.name):
                cands.append(f)
    # C3_原有项目 目录中的优先
    for f in cands:
        if "C3_原有项目" in str(f):
            return f
    return cands[0] if cands else None


_ROW_PUNCT = "。；！？：)）】》"
_LABEL_MAX = 20  # 表头单元格可作列标签的最大字数


def _linear_rows(rows: list) -> list[str]:
    """表格 → 逐行文本 (数据全量, 不截断)。

    首行若形如表头 (去重后 ≥2 格, 每格 ≤20 字且无句读) 则消费为列标签:
    数据行输出「表头：单元格」句式 (如 落实情况：…。结论：已落实);
    否则整表按「：」连接逐行输出 (历史格式)。合并单元格产生的连续重复
    内容只保留一次; 行内单元格间缺句读时补「；」, 行尾缺句号时补「。」。

    防噪 (仅表头消费后生效): 「序号」列的数字行号无信息量, 跳过 (防 "1；"
    前缀混入正文); 与表头同文的自我重复格 (双重表头行) 跳过; 表头续行的
    残片 (≤6 字且为表头文本尾部) 跳过。单元内换行拍平避免正文断行。
    """
    norm = [[re.sub(r"[\r\n]+", "", c).strip() for c in r] for r in rows]
    norm = [r for r in norm if any(r)]
    if not norm:
        return []
    header: list | None = None
    data = norm
    uniq: list[str] = []
    for c in norm[0]:
        if c and (not uniq or uniq[-1] != c):
            uniq.append(c)
    if len(uniq) >= 2 and all(len(c) <= _LABEL_MAX and not re.search(r"[。；，、：]", c) for c in uniq):
        header, data = norm[0], norm[1:]

    def _hdr_norm(h: str) -> str:
        return re.sub(r"\s+", "", h)

    def _skip(i: int, c: str) -> bool:
        if not header or i >= len(header) or not header[i]:
            return False
        h = header[i]
        if h == "序号" and re.fullmatch(r"\d+", c):  # 行号
            return True
        if c == h:  # 双重表头行: 自我重复
            return True
        hn = _hdr_norm(h)
        return len(c) <= 6 and len(hn) - len(c) >= 3 and hn.endswith(c)  # 表头续行残片

    out: list[str] = []
    for r in data:
        parts: list[str] = []
        last = None
        for i, c in enumerate(r):
            if not c or c == last:  # 空单元格 / 合并单元格重复
                continue
            last = c
            if _skip(i, c):
                continue
            lab = ""
            if header and i < len(header) and header[i] and header[i] != "序号":
                lab = header[i] + "："
            parts.append(lab + c)
        if not parts:
            continue
        line = ""
        for p in parts:
            if line and line[-1] not in _ROW_PUNCT:
                line += "；"
            line += p
        if line[-1] not in "。；！？":
            line += "。"
        out.append(line)
    return out


def extract_one(path: Path) -> dict | None:
    """解析 C3 报告 → {"sections": [...], "rules_table": {...}}"""
    try:
        import docx
        doc = docx.Document(str(path))
    except Exception:
        return None
    items = _iter_body(doc)
    sections: list[dict] = []
    cur: dict | None = None
    rules_table: dict | None = None

    for kind, val in items:
        if kind == "p":
            t = val
            if not t:
                continue
            if _TOC.search(t):  # 目录行 (带制表符+页码) → 跳过, 防假章节
                continue
            m = H_MGMT.match(t)
            is_sec = bool(m and m.group(1) in ("11", "12") and m.group(2) and
                          any(k in t for k in MGMT_TITLE_KW))
            if is_sec and m:
                if cur:
                    sections.append(cur)
                # 编号紧跟中文 (C3 格式 "11.1职业卫生管理组织机构及人员", 无空格)
                num = m.group(1) + (m.group(2) or "")
                title = re.sub(r"^\d+(\.\d+)*\s*", "", t)[:60]
                cur = {"num": num, "title": title, "paras": []}
                continue
            if re.match(r"^(11|12)(\.\d+)?[^\d]", t) and "管理" in t and len(t) < 40:
                # 11 章大标题或 12 章大标题 → 中断当前 section
                if cur:
                    sections.append(cur)
                    cur = None
                continue
            if re.match(r"^12(\s|[\u4e00-\u9fa5]|\.\d)", t) and len(t) < 40 and "。" not in t:
                # 第12章(结论)标题 → 终止收集 (防结论/附录被吸进最后一个 11.x)
                # 覆盖 "12评价结论"(无空格) / "12  结论"(双空格) / "12.1分项结论"
                if cur:
                    sections.append(cur)
                    cur = None
                continue
            if re.match(r"^附\s*(表|件|录)", t) and len(t) < 40:
                if cur:
                    sections.append(cur)
                    cur = None
                continue
            if cur is not None and not _SKIP.match(t) and len(t) >= 12:
                cur["paras"].append(t)
        else:  # tbl
            if rules_table is None:
                flat = "\n".join("\n".join(r) for r in val)
                if "职业危害防治制度" in flat or ("管理制度" in flat and "执行情况" in flat):
                    rules_table = {"cols": list(val[0]) if val else [],
                                   "rows": [list(r) for r in val[1:]]}
            if cur is not None:
                # 表格内容作为所属章节的一条事实 (拼接进 paras 供 LLM 引用; 全量不截断)
                flat = " / ".join(_linear_rows(val))
                if flat and len(flat) >= 16:
                    cur["paras"].append(f"（表）{flat}")
    if cur:
        sections.append(cur)
    if not sections:
        return None
    # 只保留管理相关章节 (11.x 全套 + 12.x 结论类不取)
    sections = [s for s in sections if s["num"].startswith("11")]
    if not sections:
        return None
    return {"sections": sections, "rules_table": rules_table,
            "src": str(path.relative_to(ROOT))}


def _resolve_pid(prefix: str) -> str:
    for p in MAT_ROOT.iterdir():
        if p.is_dir() and p.name.startswith(prefix):
            return p.name
    return prefix


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pid", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--write", action="store_true", help="落盘 data/mgmt_facts.json")
    args = ap.parse_args()

    pids = []
    if args.all:
        pids = [p.name for p in sorted(MAT_ROOT.iterdir()) if p.is_dir()]
    elif args.pid:
        pids = [_resolve_pid(args.pid)]
    else:
        ap.error("需要 pid 或 --all")

    store: dict = {}
    if OUT_PATH.exists():
        try:
            store = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        except ValueError:
            store = {}

    n_hit = 0
    for pid in pids:
        f = _find_c3(MAT_ROOT / pid)
        if not f:
            print(f"[{pid}] 无现状评价报告")
            continue
        hit = None
        try:
            hit = extract_one(f)
        except Exception as e:
            print(f"[{pid}] 解析异常: {e}")
        if not hit:
            print(f"[{pid}] 无管理章节命中 ({f.name})")
            continue
        store[pid] = hit
        n_hit += 1
        n_para = sum(len(s["paras"]) for s in hit["sections"])
        print(f"[{pid}] {len(hit['sections'])} 章节 / {n_para} 条事实 / 制度表"
              f"{len(hit['rules_table']['rows']) if hit['rules_table'] else 0}行 ← {f.name}")
        for s in hit["sections"][:3]:
            print(f"    {s['num']:<8} {s['title'][:30]:<32} {len(s['paras'])} 条")
        if len(hit["sections"]) > 3:
            print(f"    ... 共 {len(hit['sections'])} 节")

    if args.write and n_hit:
        OUT_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n✅ 已写入 {OUT_PATH.relative_to(ROOT)} ({n_hit} 项目)")
    elif n_hit:
        print(f"\n(预演) {n_hit} 项目待写入 — 加 --write 落盘")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
