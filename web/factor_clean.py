"""视觉/OCR 因素名清洗 (Q3)

用户红线: 不编造。故分两档, 边界必须清楚:

  clean_factor_name()  —— 只做**确定性**修正, 不猜内容:
    ① 明显错字 (视觉模型把"噪"认成"臭"等) → 有明确对照表才改
    ② 归一化: 全角/半角括号、去首尾空白、去重复顿号
    ③ 残缺检测: 括号不闭合/以顿号结尾 → **不改名**, 只打 needs_review 标记

  ⚠ 绝不做的:
    - 补全被截断的名字 (如 "石灰石粉尘（总尘" → 猜是"（总尘）"?) —— 可能丢的是别的字
    - 把 "呼尘）" 猜成 "石灰石粉尘（呼尘）" —— 上下文可能不同
    这些一律只标记, 交人工。

实测原始问题 (新泰 表型B):
  '臭声'                → 错字 (应为 噪声)
  '石灰石粉尘（总尘'      → 括号未闭合 (截断, 只标记)
  '呼尘）'              → 缺左括号 (截断, 只标记)
  '其他粉尘,氟及其化合物...' → 半角逗号 (归一为顿号)
"""
import re

# 视觉/OCR 常见错字 → 正确名 (只收**确定性**的, 有明确依据)
# ⚠ 保持短小: 每加一条都要能说清"为什么这个是确定的"
OCR_FIXES = {
    "臭声": "噪声",          # 视觉模型误认 (声母/字形混淆), 报告中无"臭声"此因素
    "噪声声": "噪声",
    "粉生": "粉尘",
    "高温中暑": "高温",
}

# 归一化用
_PAREN_NORM = str.maketrans({"（": "(", "）": ")", "，": ",", "；": ";", "　": " "})


def clean_factor_name(name: str) -> tuple[str, list[str]]:
    """清洗因素名 → (清洗后名字, 警告列表)

    警告非空表示"名字可能有问题但不改", 调用方应打 needs_review。
    """
    if not name:
        return "", []
    raw = str(name)
    s = raw.translate(_PAREN_NORM)
    warns = []

    # ① 明显错字 (精确匹配整个名, 或名内替换)
    if s.strip() in OCR_FIXES:
        s = OCR_FIXES[s.strip()]
        warns.append(f"错字已改正: {raw.strip()} → {s}")
    else:
        for bad, good in OCR_FIXES.items():
            if bad in s and bad != good:
                s = s.replace(bad, good)
                warns.append(f"错字已改正: {bad} → {good}")

    # ② 半角逗号 → 顿号 (多因素拼接的统一分隔符)
    if re.search(r"[,\u3001]", s):
        # 只在疑似"因素列表"时归一 (含逗号且不只一个片段)
        if "," in s and "、" not in s.split(",")[0]:
            parts = [p.strip() for p in re.split(r"[,、]", s) if p.strip()]
            if len(parts) > 1:
                s = "、".join(parts)

    # ③ 去重复顿号 / 首尾顿号
    s = re.sub(r"、{2,}", "、", s).strip("、 ").strip()

    # ④ 残缺检测 (**只标记, 不改名**)
    if s.count("(") != s.count(")"):
        warns.append(f"括号不闭合(疑截断): {s!r} — 未改动, 待人工核对")
    if raw != s and not warns:
        warns.append(f"已归一化: {raw!r} → {s!r}")

    return s, warns


def split_factors(name: str) -> list[str]:
    """多因素拼接 → 单因素列表 (拆分, 不解释内容)

    '粉尘、氟及其化合物、噪声' → ['粉尘', '氟及其化合物', '噪声']
    ⚠ 仅拆分, 不猜测/不补全。
    """
    if not name:
        return []
    s = str(name).translate(_PAREN_NORM)
    # 括号内的顿号不拆 (如 "石灰石粉尘（总尘、呼尘）")
    depth = 0
    buf = []
    parts = []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch in "、,;" and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf).strip())
    return [p for p in parts if p]


if __name__ == "__main__":
    cases = [
        "臭声",
        "石灰石粉尘（总尘",
        "呼尘）",
        "其他粉尘,氟及其化合物（不含氟化氢）（按F计）,氯化氢及盐酸,氟化氢,噪声",
        "粉尘、氟及其化合物、氟化氢、氯化氢、噪声",
        "噪声、高温、高处作业、工频电场、工频磁场",
        "石灰石粉尘（总尘、呼尘）",
        "苯乙烯",
        "、、噪声、、",
        " 高温 ",
    ]
    print(f"{'输入':<44} {'清洗后':<32} 警告")
    print("-" * 110)
    for c in cases:
        out, w = clean_factor_name(c)
        print(f"{c!r:<44} {out!r:<32} {w}")
    print()
    print("=== split_factors (拆分, 括号内不拆) ===")
    for c in ("粉尘、氟及其化合物、噪声", "石灰石粉尘（总尘、呼尘）",
              "噪声、高温、高处作业、工频电场、工频磁场"):
        print(f"  {c!r:<40} → {split_factors(c)}")
