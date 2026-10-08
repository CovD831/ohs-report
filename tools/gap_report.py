#!/usr/bin/env python3
"""生成稿 vs 真稿 内容级差距 — 最终版

核心结论:
  生成稿 = 「标题树 + 30 个有内容节点」, 存在大量**空白叶子节**。
  真稿 = 每个小节都有实打实的正文。

本脚本产出:
  1) 生成稿空白叶子节清单 (有标题、无段落无表格)
  2) 与真稿按内容块对齐的字数比
"""
import re
import sys
import zipfile
from lxml import etree

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


# ── 节标题判定 (2026-10-08 修): outlineLvl 优先, 正则兜底 ──────────────────
# ⚠ 旧版只看「数字开头且<60字」→ 正文内列举项「1）……」「1、反应方程式」被误判为
#   节标题 → 真稿 5.6 受限空间被切成 43 字(实际 1631 字), 字数比虚报 2477%。
_NUMH = re.compile(r'^(\d+(?:\.\d+)*)(?=[\s\u4e00-\u9fff“”《])\s*(\S.*)?$')


def _heading_num(el, t):
    """返回节编号; 非节标题返回 None。有 outlineLvl 即标题(最可靠判据)。"""
    pPr = el.find(W + 'pPr')
    if pPr is not None and pPr.find(W + 'outlineLvl') is not None:
        m = _NUMH.match(t)
        return m.group(1) if m else None
    m = _NUMH.match(t)
    if m and len(t) < 60 and m.group(2):
        return m.group(1)
    return None


def load(path):
    z = zipfile.ZipFile(path)
    x = etree.fromstring(z.read('word/document.xml'))
    body = x.find(W + 'body')
    out, cur = [], None
    for el in body:
        tag = etree.QName(el).localname
        if tag == 'p':
            t = ''.join(n.text or '' for n in el.iter(W + 't')).strip()
            num = _heading_num(el, t) if t else None
            if num:
                if cur:
                    out.append(cur)
                cur = [num, t, 0, 0]
            elif cur is not None:
                cur[2] += len(t)
        elif tag == 'tbl':
            n = sum(len(nn.text or '') for nn in el.iter(W + 't'))
            if cur is not None:
                cur[3] += n
    if cur:
        out.append(cur)
    return out


def main():
    gen = sys.argv[1] if len(sys.argv) > 1 else 'data/report_ada54603a9.docx'
    real = sys.argv[2] if len(sys.argv) > 2 else '/tmp/rec16_conv/长兴--预评（备案稿7-31）.docx'
    G = load(gen)
    R = load(real)

    allnums = {n for n, _, _, _ in G}
    leaves, empty = [], []
    for num, t, p, tb in G:
        is_parent = any(o.startswith(num + '.') for o in allnums)
        if not is_parent:
            leaves.append((num, t, p, tb))
            if p + tb == 0:
                empty.append((num, t))
        # 父节点自身无直属文字也算"空壳", 但不算缺陷(内容在子节)

    print(f"生成稿节点 {len(G)} | 叶子节 {len(leaves)} | 其中**空白** {len(empty)}")
    print(f"有内容的叶子节 {len(leaves)-len(empty)}")
    print()
    print("=== 空白叶子节清单 (有标题、无正文、无表格) ===")
    for num, t in empty:
        print(f"  {num:<10} {t}")

    # 与真稿按块对齐
    print()
    print("=== 与真稿(长兴)内容块对齐 ===")
    rmap = {n: (p, tb) for n, _, p, tb in R}

    def rget(*nums):
        p = tb = 0
        for n in nums:
            if n in rmap:
                p += rmap[n][0]
                tb += rmap[n][1]
        return p + tb

    PAIRS = [
        ("1.1 项目背景",        "7.1"),
        ("1.2 评价目的",        "7.2"),
        ("1.4 评价范围",        "7.4"),
        ("1.5 评价内容",        "7.5"),
        ("1.6 评价方法",        "7.6"),
        ("1.7 评价程序",        "7.7"),
        ("1.8 质量控制",        "7.8"),
        ("2.2 职业卫生管理情况", "8.1.6.2"),
        ("2.3 职业病危害防护设施", "8.1.6.3"),
        ("3.1.1 基本情况",      "8.1.1"),
        ("3.1.2 建设地点",      "8.1.2"),
        ("3.1.6 项目组成及工程内容", "8.1.4"),
        ("3.3.2 竖向布置",      "8.2.2"),
        ("3.7.2 通风空调",      "8.3.1"),
        ("3.7.3 采光照明",      "8.3.2"),
        ("4.2.3 类比防护设施",   "9.2.3/8.1.6.3"),
        ("4.2.5 类比应急救援",   "9.2.5"),
        ("4.6 类比综合结论",     "9.6"),
        ("5.1.2 识别-生产环境",  "10.1.2"),
        ("5.1.3 识别-劳动过程",  "10.1.3"),
        ("5.4.1 危害预测分析",   "10.4"),
        ("6.1.1 防尘设施",      "11.1.1"),
        ("6.1.2 防毒设施",      "11.1.2"),
        ("9.2 专项投资",        "4.6"),
        ("10.2 关键控制措施",    "4.5"),
        ("11.1 三同时",         "5.1"),
        ("11.2 补充措施及建议",   "5.2"),
        ("11.3 培训与防护",      "5.3"),
        ("11.4 警示标识",       "5.4"),
        ("11.5 职业健康监护",    "5.5"),
        ("11.6 受限空间作业",    "5.6"),
        ("11.7 应急救援措施",    "5.7"),
        ("12.1 评价结论",       "6"),
    ]
    gmap = {n: (p, tb) for n, _, p, tb in G}
    tot_r = tot_g = 0
    print(f"{'生成稿节':<24}{'真稿字':>8}{'生成字':>8}  差距")
    print('-' * 56)
    for gname, rnum in PAIRS:
        gn = gname.split()[0]
        gp, gtb = gmap.get(gn, (0, 0))
        gv = gp + gtb
        rv = rget(*rnum.split('/'))
        tot_r += rv
        tot_g += gv
        gap = "❌全空" if gv == 0 else f"{gv/rv:.0%}" if rv else "—"
        print(f"{gname:<24}{rv:>8}{gv:>8}  {gap}")
    print('-' * 56)
    print(f"{'合计':<24}{tot_r:>8}{tot_g:>8}  {tot_g/tot_r:.0%}")


if __name__ == '__main__':
    main()
