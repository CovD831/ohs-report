#!/usr/bin/env python3
"""生成稿 vs 真稿(长兴) 内容级比对 v3 — 按「小节」细粒度

v1 错: 只数段落 (生成稿正文多在表格) → 假缺失
v2 错: 按标题子串在**全文档**找 → 匹配到别章节的同名标题, 且切节跨章
v3 正: 用『编号前缀』定位小节 (如 12.1 / 5.2.1), 内容 = 该节标题到下一个
      同级或更高编号之间 (段落 + 表格)。
"""
import re
import sys
import zipfile
from lxml import etree

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def load(path):
    z = zipfile.ZipFile(path)
    x = etree.fromstring(z.read('word/document.xml'))
    body = x.find(W + 'body')
    out = []          # [(num_str, depth, title, para, tbl)]
    cur = None
    for el in body:
        tag = etree.QName(el).localname
        if tag == 'p':
            t = ''.join(n.text or '' for n in el.iter(W + 't')).strip()
            m = re.match(r'^(\d+(?:\.\d+)*)\s*(\S.*)?$', t)
            if m and len(t) < 60 and m.group(2):
                num = m.group(1)
                d = num.count('.') + 1
                if cur:
                    out.append(cur)
                cur = [num, d, t, 0, 0]
            elif cur is not None:
                cur[3] += len(t)
        elif tag == 'tbl':
            n = sum(len(nn.text or '') for nn in el.iter(W + 't'))
            if cur is not None:
                cur[4] += n
    if cur:
        out.append(cur)
    # 收拢: 每个节点 = 自身 + 所有后裔
    res = {}
    for num, d, title, p, tb in out:
        res[num] = [d, title, p, tb]
    return res, out


def subtree(out, num):
    """num 及其所有后代 (num.1 / num.1.2 ...) 的 段落+表格 合计"""
    pre = num + '.'
    p = tb = 0
    for n, d, t, pp, tt in out:
        if n == num or n.startswith(pre):
            p += pp
            tb += tt
    return p, tb


def main():
    gen = sys.argv[1] if len(sys.argv) > 1 else 'data/report_ada54603a9.docx'
    real = sys.argv[2] if len(sys.argv) > 2 else '/tmp/rec16_conv/长兴--预评（备案稿7-31）.docx'
    G, go = load(gen)
    R, ro = load(real)

    # 内容块 → (真稿编号, 生成稿编号)
    PAIRS = [
        ("1 项目背景",            "7.1", "1.1"),
        ("1 评价目的",            "7.2", "1.2"),
        ("1 评价依据",            "7.3", "1.3"),
        ("1 评价范围",            "7.4", "1.4"),
        ("1 评价内容",            "7.5", "1.5"),
        ("1 评价方法",            "7.6", "1.6"),
        ("1 评价程序",            "7.7", "1.7"),
        ("1 质量控制",            "7.8", "1.8"),
        ("2 现有企业概况",        "8.1.6", "2"),
        ("2 职业卫生管理情况",    "8.1.6.2", "2.2"),
        ("2 职业病危害防护设施",  "8.1.6.3", "2.3"),
        ("2 个人防护用品配备",    "8.1.6.4", "2.4"),
        ("2 卫生辅助用室",        "8.1.6.5", "2.5"),
        ("3 基本情况",            "8.1.1", "3.1.1"),
        ("3 建设地点",            "8.1.2", "3.1.2"),
        ("3 生产制度及定员",      "8.1.3", "3.1.5"),
        ("3 项目组成及工程内容",  "8.1.4", "3.1.6"),
        ("3 主要技术经济指标",    "8.1.5", "3.1.7"),
        ("3 总平面布置",          "8.2.1", "3.3.1"),
        ("3 竖向布置",            "8.2.2", "3.3.2"),
        ("3 通风及空气调节",      "8.3.1", "3.7.2"),
        ("3 采光照明",            "8.3.2", "3.7.3"),
        ("3 生产工艺流程",        "8.4.1", "3.5.1"),
        ("3 公用及辅助设施",      "8.4.2", "3.5.2"),
        ("3 设备布局",            "8.4.3", "3.6.2"),
        ("3 物料使用情况",        "8.5",   "3.4.2"),
        ("3 辅助用室",            "8.6",   "3.8.1"),
        ("3 建设施工工程分析",    "8.7",   "—"),
        ("4 类比企业选择",        "9.1",   "4.1"),
        ("4 类比-工作日写实",     "9.2.1", "4.2.1"),
        ("4 类比-危害因素分布",   "9.2.2", "4.2.2"),
        ("4 类比-防护设施",       "9.2.3", "4.2.3"),
        ("4 类比-防护用品",       "9.2.4", "4.2.4"),
        ("4 类比-应急救援",       "9.2.5", "4.2.5"),
        ("4 类比-职业卫生管理",   "9.3",   "4.3"),
        ("4 类比-检测内容",       "9.4.1", "4.4.1"),
        ("4 类比-检测结果",       "9.4.2", "4.4.2"),
        ("4 类比-职业健康监护",   "9.5",   "4.5"),
        ("4 类比-综合结论",       "9.6",   "4.6"),
        ("5 识别-生产工艺过程",   "10.1.1", "5.1.1"),
        ("5 识别-生产环境",       "10.1.2", "5.1.2"),
        ("5 识别-劳动过程",       "10.1.3", "5.1.3"),
        ("5 识别-建设施工过程",   "10.1.4", "—"),
        ("5 健康影响",            "10.2", "5.2"),
        ("5 职业接触限值",        "10.3", "5.3"),
        ("5 风险评价/危害程度",   "10.4", "5.4"),
        ("6 防护设施分析",        "11.1.1", "6.1"),
        ("6 防护设施评价",        "11.1.4", "6.2"),
        ("7 应急救援分析",        "11.3.1", "7.1"),
        ("7 应急救援评价",        "11.3.2", "7.2"),
        ("8 防护用品分析",        "11.2.1", "8.1"),
        ("8 防护用品评价",        "11.2.2", "8.2"),
        ("9 职业卫生管理评价",    "12.5",  "9.1"),
        ("9 专项投资评价",        "12.1/4.6", "9.2"),
        ("11 三同时",             "5.1",  "11.1"),
        ("11 补充措施及建议",     "5.2",  "11.2"),
        ("11 培训与防护",         "5.3",  "11.3"),
        ("11 警示标识",           "5.4",  "11.4"),
        ("11 职业健康监护",       "5.5",  "11.5"),
        ("11 受限空间作业",       "5.6",  "11.6"),
        ("11 应急救援措施",       "5.7",  "11.7"),
        ("12 评价结论",           "6",    "12.1"),
    ]

    print(f"{'内容块':<22}{'真稿':>10}{'生成':>10}  判定")
    print('-' * 62)
    miss, thin, ok, na = [], [], [], []
    for name, rnum, gnum in PAIRS:
        # 真稿: 支持 "a/b" 取并集
        rp = rt = 0
        for rn in rnum.split('/'):
            rn = rn.strip()
            if rn in R:
                p, t = subtree(ro, rn)
                rp += p; rt += t
        gp = gt = 0
        if gnum != '—' and gnum in G:
            gp, gt = subtree(go, gnum)
        rv, gv = rp + rt, gp + gt
        if gnum == '—' or gnum not in G:
            verdict = "❌生成缺"
            if rv: miss.append((name, rv))
        elif rv == 0:
            verdict = "真稿无"; na.append(name)
        elif gv < rv * 0.5:
            verdict = f"⚠{gv/rv:.0%}"; thin.append((name, rv, gv))
        else:
            verdict = f"✓{gv/rv:.0%}"; ok.append(name)
        print(f"{name:<22}{rv:>10}{gv:>10}  {verdict}")

    print(f"\n=== ✓{len(ok)} / ⚠偏薄{len(thin)} / ❌缺失{len(miss)} / 真稿无{len(na)} ===")
    if miss:
        print("\n❌ 缺失:")
        for n, v in miss:
            print(f"   {n}  (真稿 {v} 字)")
    if thin:
        print("\n⚠ 偏薄(<50%):")
        for n, r, g in sorted(thin, key=lambda x: x[2] / x[1]):
            print(f"   {n:<22} 真稿{r:>6} → 生成{g:>6} ({g/r:.0%})")


if __name__ == '__main__':
    main()
