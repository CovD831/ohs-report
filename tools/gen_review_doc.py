"""生成评价章结构对账单 — 真实报告 vs 我们, 供用户人工审读

输出: ~/Desktop/ohs_评价章结构对账.md
"""
import re
from collections import Counter, defaultdict
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

D = Path("/tmp/ohs_bench")
FILES = [
    "1、正力（银河基地）职业卫生预评（备案稿）0818",
    "奥绮斯摩预评-1-28修编（备案稿）",
    "格兰富  职业卫生预评（备案稿）",
    "波士胶--职业卫生预评（备案稿）0510",
    "祺珠预评-（备案稿）",
    "聚和--预评（备案稿）",
    "苏州鼎沛自动化科技有限公司  新建项目 职业卫生预评（备案稿）",
    "长兴--预评（备案稿7-31）",
]
# ⚠ 2026-10-08 修 (同 compare_real_report): 旧式「数字开头即标题」把正文列举项
#   误判为节标题 (如「1、反应方程式」「1）职业健康检查…」)。有 outlineLvl 以它为准,
#   否则严格正则: 编号后必须紧跟 空格/中文/引号/书名号。
HEAD = re.compile(r"^\s*(\d+(?:\.\d+){0,3})(?=[\s\u4e00-\u9fff“”《])\s*(\S.{0,48})$")


def _head_match(el, t):
    """段落是否节标题: outlineLvl 优先, 正则兜底。返回 match 或 None。"""
    pPr = el.find(qn("w:pPr"))
    if pPr is not None and pPr.find(qn("w:outlineLvl")) is not None:
        return HEAD.match(t)
    m = HEAD.match(t)
    return m if m and len(t) < 60 else None
SHORT = {f: f[:10] for f in FILES}


def walk(doc):
    cur = ""
    for el in doc.element.body.iterchildren():
        if el.tag == qn("w:p"):
            t = Paragraph(el, doc).text.strip()
            if not t:
                continue
            m = _head_match(el, t)
            if m:
                cur = m.group(1)
                yield ("h", m.group(1), m.group(2).strip(), cur)
            else:
                yield ("p", t, "", cur)
        elif el.tag == qn("w:tbl"):
            yield ("t", Table(el, doc), "", cur)


def main():
    # 1) 收集 11/12 章标题 (跨报告频次)
    h_cnt = Counter()
    per_doc = defaultdict(list)
    for f in FILES:
        p = D / f"{f}.docx"
        if not p.exists():
            continue
        d = docx.Document(str(p))
        for kind, a, b, _ in walk(d):
            if kind == "h" and re.match(r"^1[12](\.|$)", a):
                key = f"{a} {b}"
                h_cnt[key] += 1
                per_doc[SHORT[f]].append(key)

    # 2) 检查表登记
    tbl_cnt = Counter()
    for f in FILES:
        p = D / f"{f}.docx"
        if not p.exists():
            continue
        d = docx.Document(str(p))
        for kind, a, b, sec in walk(d):
            if kind != "t" or not a.rows:
                continue
            hdr = "".join(c.text.strip() for c in a.rows[0].cells[:6])
            is_check = (("卫生要求" in hdr or "检查依据" in hdr or "检查内容" in hdr
                         or "检查项目" in hdr)
                        and ("评价" in hdr or "检查情况" in hdr or "检查结果" in hdr
                             or "结论" in hdr))
            if is_check:
                tbl_cnt[sec.split()[0] if sec else "?"] += 1

    L = []
    L.append("# 真实报告「评价章」结构对账（8 份备案稿抽样）\n")
    L.append("> 目的: 供人工审读后决定是否把检查表从第 3 章迁到评价章\n")

    L.append("\n## 一、11/12 章标题跨报告频次（n=8）\n")
    L.append("\n| 频次 | 标题 |\n|---|---|\n")
    for k, c in sorted(h_cnt.items(), key=lambda kv: (-kv[1], kv[0])):
        L.append(f"| {c}/8 | `{k}` |\n")

    L.append("\n## 二、检查表出现在哪些小节（按表头特征识别）\n")
    L.append("\n判据: 表头含「卫生要求/检查依据/检查内容/检查项目」+「评价/检查情况/检查结果/结论」\n")
    L.append("\n| 小节 | 出现报告数 |\n|---|---|\n")
    for k, c in sorted(tbl_cnt.items(), key=lambda kv: -kv[1]):
        L.append(f"| `{k}` | {c}/8 |\n")

    L.append("""
## 三、结论（据实记录，不含推测）

### ⚠ 最关键的发现：11/12 章的正式名称是「资料性附录」

7/8 份报告里，这两章的字面标题是：

- `11 资料性附录（5）——职业病危害防护措施分析与评价`
- `12 资料性附录（6）——综合性分析与评价`

即 **11/12 章 = 附录性质**，不是主报告的正文结论章。
（这条印证了 skill 里早先的旧结论"主报告1-6章 + 附录7-12章"，我们后来实现跑偏了。）

### 7/8 一致的评价章结构

| 小节 | 内容 | 表格 | 频次 |
|---|---|---|---|
| `11.1.1` | 拟采取的职业病防护设施**分析**（防尘毒/防噪声/防高温/防电离辐射…逐项） | 描述性（无表） | 6/8 |
| `11.1.2` | 职业病防护设施**评价** | 检查表（卫生要求\|检查依据\|检查结果\|评价） | 6/8 |
| `11.2.1` | 个人职业病防护用品**分析** | 配置表（岗位\|危害因素\|防护用品\|发放周期） | 7/8 |
| `11.2.2` | 个人职业病防护用品**评价** | 检查表（检查内容\|检查依据\|检查结果\|评价） | 7/8 |
| `11.3.1` | 应急救援措施**分析** | 急救箱配置清单等 | 7/8 |
| `11.3.2` | 应急救援措施**评价** | 检查表 | 7/8 |
| `12.1` | 总体布局分析与评价 | 检查表（选址+布局**合一**） | 7/8 |
| `12.2` | 生产工艺及设备布局分析与评价 | 检查表（工艺+设备布局**合一**） | 7/8 |
| `12.3` | 建筑卫生学分析与评价 | 检查表 | 7/8 |
| `12.4` | 辅助用室分析与评价 | 表12.4-1 卫生特征分级 + 表12.4-2 辅助用室检查 | 7/8 |
| `12.5` | 职业卫生管理分析与评价 | 检查表（检查项目\|检查依据\|检查结果\|评价/结论） | 7/8 |
| `12.6` | 职业卫生专项投资分析与评价 | （我们对应 9.2 经费表） | 7/8 |

**注意 12.1/12.2 是「合一」**：真实报告把「选址+总体布局」合成 12.1、
「工艺+设备布局」合成 12.2 —— 而我们拆成 4 张表挂 4 个不同小节。

### 统一写法（逐字高度一致，可直接做模板）

- 节首引导句: `按照《工业企业设计卫生标准》（GBZ 1-2010）对{XXX}的要求设计检查表，对本项目{XXX}情况进行检查，结果见表12.X-1。`
- 表后收尾: `评价：本项目……，其检查项符合《工业企业设计卫生标准》（GBZ 1-2010）的要求。`
- 检查表列: `序号 | 卫生要求 | 检查依据 | 检查结果 | 评价`
  （管理表用「检查项目」，PPE 表用「检查内容」；末列偶用「结论」；正力/奥绮斯摩 12.5 用「合格」而非「符合」）
- 检查依据: 列到**条款级**（`GBZ1-2010 6.2.1.8`、`《职业病防治法》第二十一条`），非只写标准名
- 分析/评价**配对**：`.1 分析`（描述性文字）+ `.2 评价`（检查表）

### 我们现状差异

| 项 | 真实报告 | 我们 |
|---|---|---|
| 章节数 | **12 章**（11/12 = 资料性附录） | **11 章**（11.1 评价结论） |
| 检查表位置 | 11.1.2 / 11.2.2 / 11.3.2 / 12.1–12.5 | 散在 3.2.2 / 3.3.3 / 3.5 / 3.6.3 / 3.7.4 / 3.8.2 / 6.2 / 7.2 / 8.2 / 9.1 |
| 分析/评价配对 | 每类 `.1 分析` + `.2 评价` | 未配对 |
| 表编号 | `表12.1-1`（评价章内连续） | `表3.2-2` 等（描述章内） |
| 检查依据粒度 | 条款级 | GBZ 1 条款级（已对齐） |
| 12.1/12.2 | 选址+布局 / 工艺+设备布局 **合一** | 拆成 4 张挂 4 节 |

## 四、待用户决策

1. 是否新增第 12 章（12.1–12.6，含 12.6 专项投资），并把检查表迁过去？
2. 11 章是否补 `11.1.2 / 11.2.2 / 11.3.2`（分析/评价配对），把 6.2/7.2/8.2 的检查表迁过去？
3. `12.1`/`12.2` 是否按真实报告**合表**（选址+布局 / 工艺+设备布局）？
4. 表编号是否随迁移变为 `表12.x-1`（会影响已生成报告）？

""")
    out = Path.home() / "Desktop" / "ohs_评价章结构对账.md"
    out.write_text("".join(L), encoding="utf-8")
    print(f"已生成: {out}")
    print(f"  11/12章标题 {len(h_cnt)} 种 | 检查表小节 {len(tbl_cnt)} 处")


if __name__ == "__main__":
    main()
