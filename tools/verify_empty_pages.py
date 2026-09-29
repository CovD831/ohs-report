"""抽查: "重试后仍空"的页, 是否全部落在**附带的体检报告**里?

长兴 重试后仍空: [5, 6, 57..123]  (125页文档)
新泰 重试后仍空: 见日志             (94页文档)

若这些页全部 ≥ 文本层起始页 (长兴52 / 新泰50), 则说明它们是**附带的职业健康检查
结果报告**(本身就无职业卫生检测数据) → "空" 是正确行为, 不是漏读。

同时验证: 有文本层的页 = 体检报告; 无文本层的页 = 扫描版检测报告(需视觉提取)。
"""
import re
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CASES = [
    ('长兴', '/Users/abaaba/Desktop/长兴合成树脂/材料包_生成报告用/05_类比检测_职业卫生检测2025.pdf',
     [5, 6, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78,
      79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100,
      101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118,
      119, 120, 121, 122, 123]),
    ('新泰', '/Users/abaaba/Desktop/新泰预评价/材料包_生成报告用/05_类比检测_2025职业病危害因素检测报告.pdf',
     None),
]

for tag, path, still_empty in CASES:
    doc = fitz.open(path)
    n = doc.page_count
    # 文本层起始页
    first_text = None
    for i in range(n):
        if len((doc[i].get_text() or '').strip()) > 20:
            first_text = i + 1
            break
    print(f'=== {tag} ({n} 页, 文本层起始 p{first_text}) ===')

    # 判定: 有文本层页 是否都是体检报告
    exam_like = 0
    for i in range(n):
        t = (doc[i].get_text() or '').strip()
        if len(t) > 20 and ('体检' in t or '职业健康检查' in t):
            exam_like += 1
    with_text = sum(1 for i in range(n) if len((doc[i].get_text() or '').strip()) > 20)
    print(f'  有文本层 {with_text} 页 | 其中含"体检/职业健康检查"字样 {exam_like} 页')

    if still_empty:
        inside = [p for p in still_empty if p >= first_text]
        outside = [p for p in still_empty if p < first_text]
        print(f'  "重试后仍空" {len(still_empty)} 页:')
        print(f'    落在体检报告区(≥p{first_text}): {len(inside)} 页  '
              f'{"✓ 全部" if len(inside) == len(still_empty) else ""}')
        print(f'    落在扫描区(<p{first_text}):   {len(outside)} 页  → {outside}')
        if outside:
            for p in outside:
                t = (doc[p-1].get_text() or '').strip()
                print(f'      p{p}: {len(t)}字符 {t[:60]!r}')
    print()
