---
version: alpha
name: ohs-report-workbench
description: 职业病危害预评价报告生成工作台 — 工程精度 × 权威可信的可配置工具型UI
colors:
  primary: "#2563EB"
  primary-soft: "#DBEAFE"
  primary-strong: "#1D4ED8"
  secondary: "#0EA5E9"
  tertiary: "#F59E0B"
  success: "#16A34A"
  danger: "#DC2626"
  warning: "#F59E0B"
  neutral: "#F8FAFC"
  surface: "#FFFFFF"
  surface-muted: "#F1F5F9"
  border: "#E2E8F0"
  border-strong: "#CBD5E1"
  text: "#0F172A"
  text-secondary: "#475569"
  text-muted: "#94A3B8"
  text-inverse: "#FFFFFF"
  design-badge: "#94A3B8"
  design-badge-bg: "#F1F5F9"
typography:
  h1:
    fontFamily: PingFang SC, Noto Sans SC, system-ui, -apple-system, Segoe UI, sans-serif
    fontSize: 1.5rem
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "0em"
  h2:
    fontFamily: PingFang SC, Noto Sans SC, system-ui, -apple-system, Segoe UI, sans-serif
    fontSize: 1.25rem
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0em"
  h3:
    fontFamily: PingFang SC, Noto Sans SC, system-ui, -apple-system, Segoe UI, sans-serif
    fontSize: 1rem
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "0em"
  body-md:
    fontFamily: PingFang SC, Noto Sans SC, system-ui, -apple-system, Segoe UI, sans-serif
    fontSize: 0.875rem
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "0em"
  body-sm:
    fontFamily: PingFang SC, Noto Sans SC, system-ui, -apple-system, Segoe UI, sans-serif
    fontSize: 0.8125rem
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0em"
  caption:
    fontFamily: PingFang SC, Noto Sans SC, system-ui, -apple-system, Segoe UI, sans-serif
    fontSize: 0.75rem
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "0em"
  mono:
    fontFamily: JetBrains Mono, SF Mono, SFMono-Regular, Menlo, Consolas, monospace
    fontSize: 0.8125rem
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0em"
rounded:
  sm: 4px
  md: 8px
  lg: 12px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.text-inverse}"
    rounded: "{rounded.md}"
    padding: 8px 16px
  button-primary-hover:
    backgroundColor: "{colors.primary-strong}"
    textColor: "{colors.text-inverse}"
    rounded: "{rounded.md}"
    padding: 8px 16px
  button-secondary:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: 8px 16px
  badge-standard:
    backgroundColor: "{colors.primary-soft}"
    textColor: "{colors.primary-strong}"
    rounded: "{rounded.sm}"
    padding: 2px 8px
  badge-design:
    backgroundColor: "{colors.design-badge-bg}"
    textColor: "#475569"
    rounded: "{rounded.sm}"
    padding: 2px 8px
  badge-success:
    backgroundColor: "#DCFCE7"
    textColor: "#15803D"
    rounded: "{rounded.sm}"
    padding: 2px 8px
  badge-warning:
    backgroundColor: "#FEF3C7"
    textColor: "#B45309"
    rounded: "{rounded.sm}"
    padding: 2px 8px
  badge-danger:
    backgroundColor: "#FEE2E2"
    textColor: "#B91C1C"
    rounded: "{rounded.sm}"
    padding: 2px 8px
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: 16px
  card-hover:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: 16px
  panel-side:
    backgroundColor: "{colors.surface-muted}"
    rounded: "{rounded.md}"
    padding: 16px
  table-row:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: 8px 12px
  table-row-alt:
    backgroundColor: "{colors.surface-muted}"
    rounded: "{rounded.sm}"
    padding: 8px 12px
  table-row-hover:
    backgroundColor: "{colors.primary-soft}"
    rounded: "{rounded.sm}"
    padding: 8px 12px
  input-field:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: 8px 12px
  input-field-focus:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: 8px 12px
---

## Overview

职业病危害预评价报告生成工作台（ohs-report-workbench）——面向职业卫生评价工程师的「报告生成+可追溯」工具型 Web 应用。

**设计气质：工程精度 × 权威可信。** 这是工程师每天使用的专业工具，不是消费级产品：
- 用户：疾控中心 / 评价机构的技术人员
- 核心行为：数据输入 → 章节生成 → 查看依据 → 审核改稿
- 核心卖点：**可追溯性**（每个机械化判断都有标准条款来源）

风格基调：精确、冷静、可审计，但现代。参考 Linear 的工作流清晰度，权威蓝的政府/监管调性，数据表格的专业感。**不要**花哨渐变、大圆角游戏感、商业营销风。所有 UI 元素服务于"能否让工程师信任这个判断"。

## Colors

- **Primary (#2563EB 青蓝):** 主操作、导航、选中状态、标准依据徽标。权威蓝 —— 政府/监管调性，让工程师产生"可信"的第一印象。仅用于交互焦点与标准依据，不过度铺色。
- **Tertiary (#F59E0B 琥珀):** 警告、待审状态、设计性建议元素。区别于"标准依据"的蓝，视觉上明确"这是待人工确认的建议"。
- **Success/Danger (#16A34A / #DC2626):** 判定合格/超标、章节生成/未生成状态的语义色。
- **Neutral (#F8FAFC 底 / #FFFFFF 面 / #E2E8F0 边):** 界面骨架。浅蓝灰底 + 白卡片，低对比边线，让数据表格成为视觉主体。
- **Design badge (#94A3B8 灰):** 设计性权重依据（basis_type=design）的徽标颜色，与标准依据（蓝）形成语义区分 —— 这是本产品"诚实标注"原则的视觉载体。

## Typography

- 全部界面字体使用**系统无衬线栈**（PingFang SC / Noto Sans SC / system-ui），不加载 web 字体 —— 工具型应用追求加载速度与跨平台一致。
- **正文 14px / 行高 1.6：** 工程师阅读段落与依据说明的舒适密度。正文是默认文本尺度。
- **行高分层：** 标题 1.3-1.5，正文 1.6，表格行固定 48px，侧栏依据条目 1.5。行间距的用途是区分层级，不是装饰。
- **等宽字体 (JetBrains Mono 栈) 专用于：数值、标准号、条款号、检测数据。** 数字/标准号用等宽是"可审计"的视觉语言 —— 让工程师一眼看出"这是引用的标准/数值，不是随意填写"。
- **字体粗细克制：** 仅标题 600，正文 400，不出现 700 以上的粗体。

## Layout

- **三栏工作台布局：** 左栏 260px 章节导航树 / 中栏自适应内容区 / 右栏 320px 依据侧栏。三栏是产品核心工作流（选章节 → 看内容 → 查依据）的物理映射。
- **8px 间距网格：** 所有间距取 4/8/16/24/32 五档（xs/sm/md/lg/xl），不出现 12px 或 20px 这类非网格值。内容区内部 sm(8px) 用于小元素间隙，md(16px) 用于元素块，lg(24px) 用于章节区块。
- **内容密度：中档偏紧。** 工程师要在单屏内看到大量数据行与依据，不牺牲可读性的前提下尽量紧凑 —— 表格行 48px（8px 垂直 padding），侧栏依据条目紧凑（16px padding）。
- **宽度规则：** 内容区最大 1080px（报告正文尺度），侧栏固定 320px，不做全屏拉伸 —— 保持报告"纸面"的阅读感。
- **表格是视觉主体：** 斑马纹（surface-white / surface-muted 交替）、行 hover 浅蓝底（primary-soft）、表头 text-secondary 加粗 —— 让长表格可读可扫。

## Elevation & Depth

- 克制阴影：仅卡片和侧栏使用 `0 1px 2px rgba(15, 23, 42, 0.04)` 极浅投影，hover 升到 `0 4px 12px rgba(37, 99, 235, 0.08)`。
- 不使用大范围玻璃拟态、浮动渐变背景。深度用于"当前正在编辑的内容浮起"这一语义。

## Shapes

- **圆角克制（4/8/12px）：** 工具型 UI。小徽标 sm(4px)，输入框/卡片/按钮 md(8px)，大面板 lg(12px)。不出现 16px+ 的圆角。
- 边框 1px 实线（#E2E8F0），不用虚线框表示常态元素。

## Components

- `button-primary` **唯一的蓝色主按钮**：页面上任何时刻最多一个主操作（如"生成本章"），其余全部 `button-secondary`（浅灰底）。
- `badge-standard`（**标准依据**，蓝底）：每一条判断右侧，表示该判断有标准条款支撑 —— 本产品的信誉核心，永远清晰可见。
- `badge-design`（**设计建议**，灰底）：basis_type=design 的权重/阈值标注。灰蓝色确保它"看起来是次要的、待人工确认的"，与标准依据形成诚实区分。
- `badge-success/warning/danger`：章节状态（🟢已生成/🟡待审/🔵定稿 -> 语义化 badge）与判定结果（合格/超标）。
- `card` / `panel-side`：内容卡片与侧栏面板。侧栏用 `surface-muted` 底色区分"辅助信息"与"主内容"。
- `table-row-*`：数据表格行，斑马纹 + hover 高亮。表格单元格所有数值用 mono 字体。
- `input-field-focus`：蓝色 1px 聚焦框（不用阴影聚焦），低噪声反馈。

## Do's and Don'ts

**Do:**
- 蓝色只用于交互焦点与"标准依据"徽标，一处双职 —— 保持克制。
- 表格数字/标准号一律 mono 字体（可审计的视觉语言）。
- 依据侧栏是"对工程师负责"的承诺 —— 每个判断都必须显示依据徽标（标准蓝/设计灰二选一），没有例外。
- 用 8px 网格，间距只用 4/8/16/24/32。
- 状态用语义色（blue=生成, amber=待审, green=定稿）。

**Don't:**
- 不要渐变按钮、大圆角（>12px）、玻璃拟态 —— 这是工具不是营销页。
- 不要让"设计性建议"看起来像"标准依据" —— 灰徽标永远是灰的，不许改蓝。
- 不要把正文行高拉到 1.8+ 或压到 1.2 以下。
- 不用 700+ 粗体，不用下划线做链接（用蓝色文本 + hover）。
- 不在依据侧栏放营销文案 —— 只有判断结论、条款号、原文，最多加一个"人工核对"提示。
