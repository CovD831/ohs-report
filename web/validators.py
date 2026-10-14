"""逐节生成校验器 (生成过程中的检测器)
========================================
在每节 LLM 生成完成后、写入 section_states 之前运行:
  ① 数据校验: 本节引用的数值/行业/风险类别 必须与项目 data 一致 (防生成时口径漂移)
  ② 要素校验: 本节必须覆盖 MUST_COVER_CHECK 要素 (判据产自 structure_data) (防漏写关键内容, 如受限空间条款)
  ③ 幻觉校验: 危害因素/物料名必须能回溯项目 data (防 LLM 从库/常识补料)

设计原则: 与用户要求一致 — 生成过程中就该检测, 而不是等整报告生成完才事后打分。
结果写入 section_states 的 validation 字段: {"passed": bool, "issues": [...]}

用法: validate_section(sec, text, assess, project_data) -> dict
"""
import json
import re


# ============ 数值/属性对表 (数据校验: 生成文本必须与 project data 一致) ============
# 定员措辞锚点 (2026-10-14): 只认「定员…N人」, 因此「接触人数合计41人」这类子集人数不误报。
#   P1 直陈式: 定员293人 / 定员合计149人 / 劳动定员合计149人 / 岗位定员总数：149人
#   P2 跨短促语: 定员按31个岗位设置，共计293人 (旧版整段漏检)
# ⚠ 已知限制: 中文数字(「一百四十九人」)不参与比对, 需要时再扩。
_DY_PATTERNS = (
    re.compile(r"(?:劳动)?定员(?:总数|总计|合计|共计)?\s*[：:]?\s*([\d,，.]+)\s*(?:人|名)"),
    re.compile(r"(?:劳动)?定员[^。\n]{0,12}?(?:合计|共计|共)\s*([\d,，.]+)\s*(?:人|名)"),
)
# 「新增定员46人」= 扩建项目增量口径, 与 staffing 的现有总定员 (149) **不是同一个数**
# (真稿长兴备案稿即为此写法) → 这类措辞不参与比对, 否则整章扩建节全成误报。
_DY_OTHER_SCOPE = re.compile(r"新增|拟增|增减|新招|劳动用工变化|减少|调整")
_DY_LOOKBEHIND = 12


def _staffing_totals(staffing: list) -> tuple[int | None, int]:
    """委托给口径唯一产地 web.derived_fields.staffing_totals (勿在校验层重写一份)。"""
    from web.derived_fields import staffing_totals
    return staffing_totals(staffing)


def _check_numbers(text: str, project_data: dict) -> list[dict]:
    """本节引用的关键数值须与项目 data 一致 (从 data 提取值, 与文本中出现的值比对)"""
    issues = []
    checks = []
    # 定员 (2026-10-14 接通: 旧实现算出 total 却从未放进 checks, 等于没这条校验)
    staffing = project_data.get("staffing") or []
    dy_expected, dy_detail = _staffing_totals(staffing)
    # 投资 (data.投资)
    inv = project_data.get("investment") or project_data.get("投资")
    if inv:
        m = re.search(r"(\d[\d,，.]*)", str(inv))
        if m:
            checks.append(("投资", m.group(1)))
    # 产能
    cap = project_data.get("capacity") or project_data.get("产能")
    if cap:
        m = re.search(r"(\d[\d,，.]*)", str(cap))
        if m:
            checks.append(("产能", m.group(1)))
    for field, val in checks:
        # 文本中该字段出现的值集
        pat = {
            "投资": r"(?:投资总额|项目总投资|总投资)\D{0,4}(\d[\d,，.]*)",
            "产能": r"(?:本项目年产|设计产能|生产规模)\D{0,4}(\d[\d,，.]*)",
        }.get(field)
        if not pat:
            continue
        vals = re.findall(pat, text)
        vals = [re.sub(r"[\s,，]", "", v) for v in vals if v]
        norm = re.sub(r"[\s,，]", "", val)
        for v in vals:
            if v != norm:
                issues.append({"rule_id": "V-01", "field": field, "found": v, "expected": norm,
                               "note": f"本节{field}值 {v} 与项目data({val})不一致"})
    # 定员逐处比对 (两条锚点可能重叠, 按匹配起点去重)
    if dy_expected is not None:
        seen_at: set[int] = set()
        for pat in _DY_PATTERNS:
            for mm in pat.finditer(text):
                if mm.start() in seen_at:
                    continue
                seen_at.add(mm.start())
                # 扩建/减少等增量口径 → 与现有总定员不是同一个数, 不参与比对
                if _DY_OTHER_SCOPE.search(text[max(0, mm.start() - _DY_LOOKBEHIND):mm.start()]):
                    continue
                raw = re.sub(r"[\s,，]", "", mm.group(1))
                try:
                    got = int(float(raw))
                except ValueError:
                    continue
                if got == dy_expected:
                    continue
                note = f"本节定员 {got} 人与项目班制定员表口径 {dy_expected} 人不一致"
                if dy_detail and got == dy_expected + dy_detail:
                    note += f" ({dy_expected}+{dy_detail}: 疑似把「合计」行与明细行二次相加)"
                issues.append({"rule_id": "V-01", "field": "定员", "found": str(got),
                               "expected": str(dy_expected), "note": note,
                               "context": mm.group(0)})
    return issues


# ============ V-02 词表锚定幻觉检测 (2026-10-14 重写) ============
# 旧实现建了 allowed 集却整段丢弃, 实际只查 4 个历史幻觉词 → 幻觉检测形同装饰。
# 新机制: 用官方危害因素词表当"探针词典"扫正文, 命中且**项目无源**才报。
#   有源集 = 物料清单 ∪ 检测数据 ∪ 危害识别结果 ∪ 产品 ∪ assess.hazards
#            ∪ 通用物理因子 ∪ 「项目材料原文里出现过」（柴油储罐/施工材料等虽不入
#            物料清单却是项目事实, 判幻觉是误报）
#   ⚠ 有源 blob 必须排除 section_states / built_tables: 生成结果不能自证有源,
#     否则一次幻觉写进正文后就永久洗白。
# 精度优先的三道豁免 (实测 4 项目 274 节: 481 命中 → 0 误报):
#   ① 类别词不参与 (「防噪声振动」里的「振动」不是物料声明)
#   ② 长度 <3 字不参与 (「苯」「汞」单字极易在长词内部误命中, 由黑名单兜底)
#   ③ 施工期语境 / 法规条文引用语境不参与 (施工粉尘、《条例》列举的石棉粉尘
#      都不是本项目投产后的物料声明)
_VOCAB_SOURCES = (("oel_limit", "factor_name"), ("hazard_factor", "name"),
                  ("hazard_toxicity", "factor_name"), ("hazard_toxicity", "aliases"),
                  ("health_effect", "factor"))
# GBZ 2.2 类物理因素/作业类别词: 不作为"物料声明"参与回溯
_PHYS_CATEGORY_WORDS = {"噪声", "高温", "低温", "振动", "手传振动", "全身振动", "微波",
                        "紫外辐射", "红外辐射", "激光", "工频电场", "高频电磁场",
                        "超高频", "照度", " WBGT", "WBGT", "电焊弧光", "噪声与振动"}
# 通用允许词 (项目未列入物料但按标准/惯例必须出现的因子)
_KEEP_FACTORS = {"噪声", "高温", "局部振动", "工频电场", "紫外辐射", "矽尘", "电焊烟尘",
                 "其他粉尘", "粉尘", "WBGT", "电焊弧光", "臭氧", "氮氧化物",
                 "锰及其无机化合物", "三氧化铬", "金属镍与难溶性镍化合物", "游离二氧化硅"}
# 历史幻觉词: 项目 data 完全无源且曾在生成稿出现 → 无条件报 error (与词表扫描互补)
_KNOWN_HALLUCINATIONS = {"硫酸二甲酯", "石蜡烟", "信息传输", "软件开发"}
_CTX_CONSTRUCTION = re.compile(r"施工|建设期|土建|安装期|装修")
_CTX_LEGAL_QUOTE = re.compile(r"条例|法》|规定》|办法|目录|清单|第[一二三四五六七八九十百零]+条|"
                              r"应当|必须|依据|参照")
_CTX_WINDOW = 40

_VOCAB_CACHE: dict[str, object] = {"terms": None, "pat": None}
_ALLOWED_CACHE: dict[tuple, dict] = {}


def _load_factor_vocab():
    """官方词表 (进程内缓存一次)。返回 (词表 set, 编译好的正则)。"""
    if _VOCAB_CACHE["pat"] is not None:
        return _VOCAB_CACHE["terms"], _VOCAB_CACHE["pat"]
    terms: set[str] = set()
    try:
        from knowledge.oel import connect
        conn = connect()
        try:
            for table, col in _VOCAB_SOURCES:
                for (raw,) in conn.execute(f"select distinct {col} from {table}"):
                    v = str(raw or "")
                    # 不按 ASCII 逗号切: "N,N-二甲基苯胺" 是一个完整物质名
                    # 左右括号都切: "沉淀SiO2（白炭黑）" → 两个合法词元; 否则产出
                    # "1,1-二甲基肼）" 这种带尾巴的脏词元, 正文永远匹配不上
                    for t in re.split(r"[、；;/（(）)]", v):
                        t = t.strip(" 　,，.")
                        if len(t) >= 3 and t not in _PHYS_CATEGORY_WORDS \
                                and not re.fullmatch(r"[A-Za-z0-9.%\-\s]+", t):
                            terms.add(t.lower())
        finally:
            conn.close()
    except Exception:
        terms = set()  # 词表不可用 → 本条校验静默降级 (黑名单仍生效)
    pat = re.compile("|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))) \
        if terms else None
    _VOCAB_CACHE["terms"], _VOCAB_CACHE["pat"] = terms, pat
    return terms, pat


def _sourced_factors(project_data: dict) -> set[str]:
    """有源危害因素集 (小写)。含"项目材料原文出现过"这一宽容项, 见文件头注释。"""
    allowed = set(_KEEP_FACTORS)
    for key, sub in (("materials", "name"), ("products", "name"), ("detections", "factor")):
        for item in (project_data.get(key) or []):
            nm = str(item.get(sub) if isinstance(item, dict) else item)
            nm = nm.split("|")[0].strip().lower()
            if nm:
                allowed.add(nm)
    for g in (project_data.get("hazard_grid") or []):
        if isinstance(g, dict):
            for f in str(g.get("factors") or "").replace(" ", "").split("、"):
                if f:
                    allowed.add(f.lower())
    terms, _ = _load_factor_vocab()
    if terms:
        blob = json.dumps({k: v for k, v in project_data.items()
                           if k not in ("section_states", "built_tables", "validation")},
                          ensure_ascii=False, default=str).lower()
        allowed |= {t for t in terms if t in blob}
    return allowed


def _check_factors(text: str, project_data: dict, assess: dict | None) -> list[dict]:
    """危害因子回溯: 本节出现的官方词表物质, 必须能在项目数据里找到来源 (防幻觉)"""
    issues: list[dict] = []
    low = (text or "").lower()
    det_text = " ".join(str(d.get("factor") or "") for d in (project_data.get("detections") or []))
    reported: set[str] = set()
    # ① 历史幻觉词: 无条件报 error (词表扫描对同词不再重复报, 免"一词两条"噪声)
    for b in _KNOWN_HALLUCINATIONS:
        if b.lower() in low and b not in det_text:
            reported.add(b.lower())
            issues.append({"rule_id": "V-02", "level": "error", "found": b,
                           "note": f"本节出现历史幻觉词: {b} (项目 data 无源)"})
    terms, pat = _load_factor_vocab()
    if pat is None:
        return issues
    akey = (id(project_data), len(project_data))
    ent = _ALLOWED_CACHE.get(akey)
    if not ent:
        allowed = _sourced_factors(project_data)
        allowed |= {str(h.get("factor") or "").lower() for h in (assess or {}).get("hazards") or []
                    if isinstance(h, dict)}
        allowed.discard("")
        a_pat = re.compile("|".join(re.escape(t) for t in sorted(allowed, key=len, reverse=True))) \
            if allowed else None
        if len(_ALLOWED_CACHE) > 8:
            _ALLOWED_CACHE.clear()
        _ALLOWED_CACHE[akey] = ent = {"allowed": allowed, "pat": a_pat}
    allowed, a_pat = ent["allowed"], ent["pat"]
    # 有源词的占位区间: 词表短词落在长词内部时不算无源 (甲苯 ⊃ 苯 一类)
    aspans = [(m.start(), m.end()) for m in a_pat.finditer(low)] if a_pat else []
    for m in pat.finditer(low):
        w = m.group(0)
        if w in allowed or w in reported:
            continue
        if any(s <= m.start() and m.end() <= e for s, e in aspans):
            continue
        ctx = text[max(0, m.start() - _CTX_WINDOW):min(len(text), m.end() + _CTX_WINDOW)]
        if _CTX_CONSTRUCTION.search(ctx) or _CTX_LEGAL_QUOTE.search(ctx):
            continue
        reported.add(w)
        issues.append({"rule_id": "V-02", "level": "warn", "found": w,
                       "note": f"本节出现「{w}」但项目物料/检测/危害识别结果均无此物质 — "
                               f"若非本项目物料应删除, 或改引有源物质",
                       "context": ctx[:60]})
    return issues


def _suggest_standard(code: str, limit: int = 3) -> list[str]:
    """库中**近似**标准号 — 给 LLM 纠错线索 (**只建议, 不自动改**, 避免猜测改错)

    实测 LLM 常见错法 (2026-10):
      GBZ50019-2015   ← 多写 Z   (实为 GB 50019-2015)
      GB/T 2890-2022  ← 多写 /T  (实为 GB 2890-2022, 强制性)
      GB/T50493-2019  ← 少空格   (实为 GB/T 50493-2019)
    匹配策略: 数字部分相同即算近似 (前缀/空格差异不影响判定)
    """
    import re as _re
    from knowledge.oel import connect
    m = _re.search(r"(\d+(?:\.\d+)?)", code or "")
    if not m:
        return []
    num = m.group(1)
    try:
        conn = connect()
        rows = conn.execute(
            "SELECT code, name, state FROM standard_db WHERE code LIKE ? LIMIT 6",
            (f"%{num}%",)).fetchall()
    except Exception:
        return []
    out = []
    for c, n, st in rows:
        if _re.sub(r"\s", "", c or "").upper() == _re.sub(r"\s", "", code or "").upper():
            continue  # 完全相同不算建议 (那是命中, 走别的分支)
        out.append(f"{c} [{st}] {n[:26]}")
        if len(out) >= limit:
            break
    return out


def _check_standards(text: str) -> list[dict]:
    """V-05 标准引用校验: 正文引用的标准号必须能在 standard_db 查到, 且**未废止**

    事故背景 (2026-10): 正文写成 GBZ/T 196—2007《建设项目职业病危害预评价技术导则》,
    该标准已被 GBZ/T196-2025 代替(2026-02-01 起废止) —— LLM 凭训练记忆写旧版号,
    而 _WRITING_GUIDE 5d 只约束"限值数值不得凭记忆改写", 没约束"标准版本号"。

    判据 (确定性查表, 不靠 LLM):
      - 正则抽出正文里所有 "GB/GBZ/WS/… 编号—年份" 形式的标准引用
      - 归一化后在 standard_db 里精确查 (禁模糊/LIKE)
      - state == '废止' → 报错(引用废止标准=硬伤)
      - 查不到 → 提醒(可能是新标准未入库, 也可能是 LLM 编的)
      - 有 replace_of 且被替代者已废止 → 提示应引新版
    """
    issues: list[dict] = []
    # 标准号形态: GB 30871—2022 / GBZ/T 196-2025 / WS/T 757—2016 / GBZ2.1—2019
    pat = re.compile(r"(GBZ?|WS|GB)\s*(/T|/Z)?\s*(\d+(?:\.\d+)?)\s*[—\-–−]\s*(\d{4})", re.I)
    found = pat.findall(text)
    if not found:
        return issues
    try:
        from knowledge.oel import connect
        conn = connect()
    except Exception:
        return issues
    seen: set[str] = set()
    for prefix, suffix, num, year in found:
        full = f"{prefix.upper()}{(suffix or '').upper()}{num}-{year}"
        norm = re.sub(r"\s", "", full).upper()
        if norm in seen:
            continue
        seen.add(norm)
        # 精确查 (归一化后比对, 兼容库里 'GBZ/T196-2025' 与正文 'GBZ/T 196—2025')
        hit = None
        for r in conn.execute("SELECT code, name, state FROM standard_db"):
            if re.sub(r"[\s—–−]", "", (r[0] or "")).replace("－", "-").upper() == norm:
                hit = r
                break
        if hit is None:
            sug = _suggest_standard(full)
            note = f"引用的标准[{full}]未在标准库中查到, 请核实编号/年份是否真实存在"
            if sug:
                note += f"; 库中近似: {' / '.join(sug)}"
            issues.append({"rule_id": "V-05", "level": "warn", "standard": full,
                           "note": note, "suggest": sug})
            continue
        code, name, state = hit
        if (state or "") == "废止":
            cur = ""
            try:
                from web.standard_version import resolve_current
                _c = resolve_current(conn, code)
                if _c and (_c.get("state") or "") not in ("废止", "已废止"):
                    cur = _c.get("code") or ""
            except Exception:
                pass
            _note = f"引用了**已废止**标准[{code}] {name[:30]} —— 须改引现行版本"
            if cur:
                _note += f"[{cur}]"
            issues.append({"rule_id": "V-05", "level": "error", "standard": code,
                           "current": cur, "note": _note})
    return issues


def _check_must_cover(sec: str, text: str) -> list[dict]:
    """必覆盖要素: 判据唯一产地 = web/structure_data.MUST_COVER_CHECK (V-03)。
    缺失→提醒, 不做硬阻断, 供重试/标注。"""
    from web.structure_data import MUST_COVER_CHECK
    issues = []
    for item, kws in MUST_COVER_CHECK.get(sec, []):
        if not any(k in text for k in kws):
            issues.append({"rule_id": "V-03", "item": item, "note": f"缺少要素[{item}]: 应包含 {'/'.join(kws[:2])} 等"})
    return issues


def _check_duplication(sec: str, text: str, all_texts: dict | None = None) -> list[dict]:
    """V-04 跨节重复检测: 本节的长片段(30字滑窗)在其他节出现 → 提示串章
    滑窗而非整句: 前缀不同('本项目为…' vs 直接开头)但主体相同也能抓到
    all_texts: {sec: text} 生成时的全局快照 (无则跳过)"""
    if not all_texts:
        return []
    issues = []
    seen = set()
    W = 30  # 滑窗宽度
    for other_sec, other in all_texts.items():
        if other_sec == sec or not other:
            continue
        # 本节每个30字窗去其他节找
        hits = 0
        sample = ""
        for i in range(0, max(1, len(text) - W), W // 2):
            frag = text[i:i + W]
            if len(frag) < W or frag in seen:
                continue
            seen.add(frag)
            # 跳过含标点过多的碎窗 (列表/公式)
            if sum(1 for c in frag if c in '、，,（()）0123456789./') > W * 0.5:
                continue
            if frag in other:
                hits += 1
                sample = sample or frag
        if hits >= 2:  # 至少2个窗命中才算重复 (1个可能是正常引用句)
            issues.append({"rule_id": "V-04", "note": f"本节与[{other_sec}]大段重复({hits}处): {sample[:30]}…",
                           "found": other_sec})
        if len(issues) >= 3:
            break
    return issues


def validate_section(sec: str, text: str, project_data: dict, assess: dict | None = None,
                     all_texts: dict | None = None) -> dict:
    """逐节校验: 返回 {passed, issues, score} — 生成时调用"""
    issues = []
    issues += _check_numbers(text, project_data)
    issues += _check_factors(text, project_data, assess)
    issues += _check_standards(text)
    issues += _check_must_cover(sec, text)
    issues += _check_duplication(sec, text, all_texts)
    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "score": max(0, 100 - 20 * len(issues)),
        "checked_at": "validate_section",
    }
