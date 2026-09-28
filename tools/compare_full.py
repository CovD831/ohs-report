"""生成报告 vs 原报告 逐章比对 (含 2007→2025 结构映射)

⚠ 前提 (必须先理解, 否则会把"标准换代"误报成"缺章"):
   原报告(长兴备案稿) = GBZ/T 196—**2007** 结构: 主报告1-6章 + 资料性附录1-6
   生成系统      = GBZ/T 196—**2025** 结构: 平铺1-12章 (标准10.2.1~10.2.12)
   两者章节号天然不同 → 必须按**内容等效**映射后再比, 不能直接对号。

映射依据: 标准 10.2 各条内容 + 原报告各章实际内容。
"""
import json
import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

GEN = Path(sys.argv[1] if len(sys.argv) > 1 else
           "/Users/abaaba/Projects/ohs-report/data/report_c8c7ff0a4d.docx")

# 原报告(2007版) → 生成报告(2025版) 内容等效映射
# 值 = (说明, [生成侧要累加的章节号列表])
MAP = {
    "1 建设项目概况": ("2 现有企业概况 + 3.1 工程概况", ["2", "3.1"]),
    "1.1 基本情况": ("3.1.1 基本情况", ["3.1.1"]),
    "2 职业病危害因素识别与评价": ("5 危害因素及危害程度", ["5"]),
    "2.1 职业病危害因素识别": ("5.1 危害因素识别", ["5.1"]),
    "2.2 职业病危害因素风险评价": ("5.4 危害程度分析", ["5.4"]),
    "3 职业病危害防护措施评价": ("6 防护设施分析+评价", ["6"]),
    "3.1 职业病防护设施评价": ("6.1+6.2", ["6.1", "6.2"]),
    "3.2 个人使用的职业病防护用品评价": ("8 个人防护用品", ["8"]),
    "3.3 应急救援措施评价": ("7 应急救援措施", ["7"]),
    "4 综合性评价": ("3.2~3.8 + 9 + 10", ["3.2", "3.3", "3.7", "3.8", "9", "10"]),
    "4.1 选址、总体布局评价": ("3.2+3.3", ["3.2", "3.3"]),
    "4.2 生产工艺及设备布局评价": ("3.5+3.6", ["3.5", "3.6"]),
    "4.3 建筑卫生学评价": ("3.7 建筑卫生学", ["3.7"]),
    "4.4 辅助用室评价": ("3.8 辅助用室", ["3.8"]),
    "4.5 职业卫生管理评价": ("9 职业卫生管理", ["9"]),
    "4.6 职业卫生专项投资评价": ("9.2 专项投资", ["9.2"]),
    "5 职业病补充措施及建议": ("11 补充建议", ["11"]),
    "6 评价结论": ("12 结论与建议", ["12"]),
    "7 资料性附录（1）——评价要点": ("1 总论", ["1"]),
    "8 资料性附录（2）——工程分析": ("3 建设项目工程分析", ["3"]),
    "9 资料性附录（3）——类比调查分析": ("4 类比企业调查分析", ["4"]),
    "10 资料性附录（4）——职业病危害因素识别与评价": ("5 危害因素及危害程度", ["5"]),
    "11 资料性附录（5）——职业病危害防护措施分析与评价": ("6+7+8", ["6", "7", "8"]),
    "12 资料性附录（6）——综合性分析与评价": ("3.2-3.8+9+10", ["3.2", "3.3", "3.7", "3.8", "9", "10"]),
}


def iter_blocks(doc):
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


_HEAD = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[\s、.．]+(\S.{0,38})$")
# 生成报告的标题形如 '1  总论' (双空格分隔), 且全用 Normal 样式 → 只能按模式识别
_HEAD_GEN = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s{2,}(\S.{0,38})$")


def is_head(t: str, gen: bool = False):
    t = (t or "").strip()
    if not t or len(t) > 46 or t.endswith(("。", "；", "，", ",", ";", "：", ":")):
        return None
    m = (_HEAD_GEN if gen else _HEAD).match(t)
    if not m:
        return None
    num, txt = m.group(1), m.group(2).strip()
    if not txt or txt[0].isdigit():
        return None
    return num, txt


def profile(path: Path, gen: bool = False) -> dict:
    doc = docx.Document(str(path))
    heads, caps, per = [], [], {}
    cur = None
    chars = 0
    for b in iter_blocks(doc):
        if isinstance(b, Paragraph):
            t = b.text.strip()
            if not t:
                continue
            chars += len(t)
            h = is_head(t, gen)
            if h:
                heads.append((h[0].count(".") + 1, h[0], h[1]))
                cur = h[0]
                per.setdefault(cur, {"chars": 0, "tables": 0, "title": h[1]})
            if re.match(r"^表\s*[\d.\-—]+", t):
                caps.append(t[:60])
            if cur:
                per.setdefault(cur, {"chars": 0, "tables": 0, "title": ""})["chars"] += len(t)
    return {"heads": heads, "caps": caps, "per": per, "chars": chars,
            "tables": len(doc.tables), "paras": len(doc.paragraphs)}


def main():
    if not GEN.exists():
        print(f"生成报告不存在: {GEN}")
        return 1
    g = profile(GEN, gen=True)
    o = profile(Path("/tmp/bench_orig/长兴--预评（备案稿7-31）.docx"))

    print("=" * 84)
    print("总量对比")
    print(f"{'':16}{'原报告(2007版)':>18}{'生成报告(2025版)':>18}")
    for k, lbl in (("paras", "段落"), ("chars", "字符"), ("tables", "表格"), ("caps", "表题")):
        ov = o[k] if k != "caps" else len(o["caps"])
        gv = g[k] if k != "caps" else len(g["caps"])
        print(f"{lbl:16}{ov:>18}{gv:>18}")
    print(f"{'章节标题':16}{len(o['heads']):>18}{len(g['heads']):>18}")
    print()
    print("=" * 84)
    print("章节映射对比 (2007 原报告 → 2025 生成报告)")
    print(f"{'原报告章节':44}{'等效生成章节':28}{'原字数':>7}{'生字数':>7}")
    print("-" * 84)
    okeys = {num: d for num, d in o["per"].items()}

    def _subtree(per: dict, prefix: str) -> int:
        """章节字数 = 该节 + 所有后缀子节之和 (正文常写在叶子节, 章级本身为空)"""
        tot = 0
        for n, d in per.items():
            if n == prefix or n.startswith(prefix + "."):
                tot += d.get("chars", 0)
        return tot

    print(f"{'原报告章节':40}{'原字':>6}{'生字':>7}{'比':>7}  等效生成章节")
    print("-" * 96)
    tot_o = tot_g = 0
    for k, (desc, nums) in MAP.items():
        num = k.split()[0]
        ov = _subtree(o["per"], num)
        gv = sum(_subtree(g["per"], n) for n in nums)
        tot_o += ov
        tot_g += gv
        ratio = f"{gv / ov * 100:.0f}%" if ov else "—"
        flag = "" if ov == 0 or gv >= ov * 0.6 else ("  ⚠偏少" if gv else "  ✗空")
        print(f"{k:40}{ov:>6}{gv:>7}{ratio:>7}  {desc}{flag}")
    print("-" * 96)
    print(f"{'合计(映射覆盖部分)':40}{tot_o:>6}{tot_g:>7}"
          f"{(tot_g / tot_o * 100 if tot_o else 0):>6.0f}%")
    print()
    print("=" * 84)
    print("=== 生成报告 一级章节 ===")
    for lvl, num, txt in g["heads"]:
        if lvl == 1:
            d = g["per"].get(num, {})
            print(f"  {num} {txt:36} 字数{d.get('chars', 0):>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
