#!/usr/bin/env python3
"""通用护栏 (C): 报告印出的每个标准号都必须能在真实源头对上; 对不上 = 红。

为什么存在 (2026-10 事故, 用户拍板"修复要通用"):
    既有 web/std_guard.py 只扫"引用了 standard_db 里 state=废止 的号", 扫不出
    "引用了一个**目录里根本不存在**的号"。GBZ 2.2—2019 这个幽灵正是从这条缝里漏出去的:
    它在 standard_db 里被 report_evidence 自造了一行"实施中", 于是既不被判废止、也不被
    判"不存在", 一路印进 4 份成品。本护栏补的就是这个洞: 只认**一手/权威来源**, 对不上就是红。

判据口径 (严格遵守既有规矩):
  - 判据纯数据派生, **不硬编码任何标准号清单**。集合来源 = 真实源头:
      (1) 仓库自抓国家卫健委一手目录快照  acquire/raw/nhc_gbz/gbz_baseline.json
      (2) standard_db 里**权威来源**行 (source 非 'report_evidence' —— report_evidence 是
          自生成、一手快照里没有的号, 正是幽灵的来源; 其余 nhc_catalog/samr/gov_pdf/confirmed
          /manual 都是抓来自查的官方数据)。
  - 采集尺子 = knowledge.standard_version.STD_COLLECT_PAT (2026-10-16 #43 起不再是本文件
      自带的第二把尺子): 族名单/连接号字符类与改写尺子 STD_REF_PAT **同源**, 只放宽三处
      —— 年份收任意 4 位数字、推荐性标记允许无斜杠形态、族名单含 HG。
      放宽的理由与边界都写在 standard_version.py 里 (改写尺子为什么仍收窄: 库里没有
      HG 的行, 扩到改写面=凭空造 4 条假告警; 采集面扩了才会**逼出核实**)。
  - 比对口径 = knowledge.standard_version.std_key (身份键+年份), 不是 norm_code 的字符串相等:
      'GBZT189.9-2025' 与 'GBZ/T 189.9—2007' 是同族两版, 'HGT20698-2009' 与
      'HG/T 20698-2009' 是同一个号 —— 书写形态不该影响"存不存在"的答案 (#42 同一根因)。
      归一靠**字符类/形态规则**, 不是同义词表 (见 M2/M8 变异)。
  - 容器无 .git → 不写任何"与 HEAD 比"的判据。
  - 豁免双向对账 (`std_code_exempt.json`, 随代码走的数据文件, 不是代码里的清单):
      正向: 印出号 ∉ (源头 ∪ 已核实豁免) → 未登记 → 点名 (必须为 0 才算绿);
      反向: 每条豁免必须在被扫报告里命中 ≥1 次, 否则是僵尸条目 → 点名。
      ⚠ 豁免的语义是"**已核实**但不在卫生目录内", 不是"护栏看不见" (#43 用户红线):
        登记项 status='待复核' (一手核实不到) **不计豁免 → 继续红**;
        '一手已核实' 的条目必须在 reason 里留一手出处 URL, 否则点名。
  - 采集面盲区探测 (M7): 宽松尺子 STD_LOOSE_PAT 收到任意 2-4 字母族, 命中里凡是
      采集尺子没覆盖的形态 → 点名。这样"下一个 HG"不会再以"两把尺子都看不见"的形式
      出现 (N1 的教训不是"HG 要加进名单", 而是"名单外必须出声")。
  - 每条被当证据的检查都配有一条"必然能红、且每次都红"的变异用例 (M1..M8)。
  - 源头读不到 → 先短路成一条点名, 不产出成堆假绿/假红 (见 origins_short_circuit)。

用法:
    .venv/bin/python tools/test_std_existence.py                 # 判据自检 + 扫 /tmp/fp_docx
    .venv/bin/python tools/test_std_existence.py --docx DIR      # 扫指定 docx 目录
    .venv/bin/python tools/test_std_existence.py --regen          # 现生成 4 项目成品再扫 (依赖导出链)

⚠ 只在本机跑, **不登记进 GUARD_TESTS** (交付链能力边界, 写在这里免得下轮误加):
    源头(1) 是 `acquire/raw/nhc_gbz/gbz_baseline.json`, 而 quick_deploy.sh 的 `_EXCL`
    排除 `raw`/`*/raw` (acquire/raw 有 2.8GB 标准 PDF) → 镜像里没有这份快照。
    放进容器里跑, `load_origins` 会给出"读不到一手目录快照"的诊断并短路成一条点名 ——
    那是**必然红**而非判据有效。它仍在 MD5_FILES 里 (改动了要送得进去、要能比对 md5)。
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.standard_version import (        # noqa: E402  尺子全部单一产地
    STD_COLLECT_FAMS, STD_COLLECT_PAT, STD_FAMS, STD_LOOSE_FAMS, STD_SEPS,
    collect_code, norm_code, std_key)

# 宽松尺子 (只用于"采集面盲区探测", 不参与存在性判断): 族位置换成任意 2-4 个大写字母。
#   前缀分支同样整体括住 + 用同一个 STD_SEPS 字符类, 与两把正式尺子只差"族名单"一处。
STD_LOOSE_PAT = re.compile(
    r"(?<![0-9A-Za-z])(" + STD_LOOSE_FAMS + r")(?:\s*/?\s*([A-Z]))?\s*"
    r"(\d+(?:\.\d+)?)"
    r"\s*[" + STD_SEPS + r"]\s*(\d{4})(?![0-9A-Za-z])")

BASELINE = ROOT / "acquire" / "raw" / "nhc_gbz" / "gbz_baseline.json"
# 库路径走单一产地 knowledge.paths.DB_PATH (认 OHS_DATA_DIR): 上一版在这里硬写
#   ROOT/"data"/"ohs.db" → 部署演练把库搬到临时树时这支守卫读的还是真实库, 等于没演。
from knowledge.paths import DB_PATH  # noqa: E402
DB = DB_PATH
UNTRUSTED_SOURCE = "report_evidence"     # 唯一不被当"真实源头"的来源 (自造、非一手)

n_pass = n_fail = 0


def check(label, cond, detail=""):
    global n_pass, n_fail
    if cond:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label} {detail}")



# ── 源头 (真实来源派生, 不点名任何标准号) ─────────────────────────────────────
def load_origins(baseline_path=BASELINE, db_path=DB):
    """返回 (存在性口径的源头号集合, 诊断信息 list)。诊断非空 = 某个源头读不到 (上层须短路)。"""
    diag = []
    origins = set()
    import json
    try:
        data = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
        codes = [e.get("code") for e in data if isinstance(e, dict) and e.get("code")]
        if not codes:
            diag.append(f"目录快照 {baseline_path.name} 解析出 0 条 code (空/格式变) → 源头失明")
        for c in codes:
            origins.add(std_key(c))
    except (OSError, ValueError) as e:
        diag.append(f"读不到一手目录快照 {baseline_path}: {e}")

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            # code 与 replace_of 都收: replace_of 指向的旧号也是权威登记的一部分
            for (val,) in conn.execute(
                "SELECT code FROM standard_db WHERE source IS NULL OR source != ?",
                (UNTRUSTED_SOURCE,),
            ):
                if val:
                    origins.add(std_key(val))
        finally:
            conn.close()
    except sqlite3.Error as e:
        diag.append(f"读不到 standard_db: {e}")
    return origins, diag


TIERS = ("一手已核实", "待复核", "存量(未标 status)")


def load_exempt(path=None):
    """std_code_exempt.json → {归一号: {reason, status, tier}}。

    2026-10-16 从官方层表搬成 JSON (用户拍板: 官方层只装官方来源数据, 豁免表是校验配置)。
    2026-10-16 (#43) 再分两层: status='一手已核实' 才**挡得住红**; '待复核' 只登记不动作,
      因为"护栏看不见"不是豁免理由 —— 把核实不到的号写进豁免来止血 = 复刻 GBZ 2.2—2019
      的成因 (那条幽灵的成因正是"没人对它做存在性判断")。存量条目 (无 status) 按老口径继续
      计入豁免, 但会被点名"缺一手出处 URL", 等下一批逐条补。
    文件读不到 = **配置事故**, 不静默当成"零豁免": 那样正向会把一堆正常号报成幽灵、
    或者反向僵尸检查悄悄失去被测对象 —— 两种都是把部署问题伪装成内容问题。
    """
    import json as _json
    from knowledge.paths import STD_CODE_EXEMPT_FILE
    p = Path(path or STD_CODE_EXEMPT_FILE)
    if not p.exists():
        raise SystemExit(f"❌ 豁免登记 {p} 读不到 —— 先修交付链 (Dockerfile COPY / MD5_FILES), "
                         f"别把配置缺失当成'有人引用了幽灵号'")
    entries = _json.loads(p.read_text(encoding="utf-8")).get("entries") or []
    out = {}
    for e in entries:
        if not e.get("code"):
            continue
        st = (e.get("status") or "").strip()
        reason = e.get("reason") or ""
        tier = st if st in ("一手已核实", "待复核") else "存量(未标 status)"
        out[std_key(e["code"])] = {"code": e["code"], "reason": reason,
                                   "status": st, "tier": tier,
                                   "has_url": "http" in reason.lower()}
    return out


def honored(exempt: dict) -> set:
    """能挡红的登记项 = 一手已核实 ∪ 存量; '待复核' 不在内 (继续红)。"""
    return {k for k, v in exempt.items() if v["tier"] != "待复核"}


def _exist_code(m: re.Match) -> str:
    """采集尺子一次命中 → 存在性比对口径 (单一产地: collect_code + std_key)。"""
    return std_key(collect_code(m))


def printed_codes(text: str) -> set[str]:
    """任意产物文本 → 印出的标准号 (存在性口径) 集合。采集尺子见 STD_COLLECT_PAT。"""
    return {_exist_code(m) for m in STD_COLLECT_PAT.finditer(text or "")}


def printed_raw_forms(text: str) -> dict[str, str]:
    """口径号 → 一个真实书写形态 (点名时给复核的人看原文怎么印的)。"""
    out: dict[str, str] = {}
    for m in STD_COLLECT_PAT.finditer(text or ""):
        out.setdefault(_exist_code(m), m.group(0))
    return out


def offsurface_forms(text: str) -> list[str]:
    """采集面盲区: 宽松尺子命中、而采集尺子在同一段落没命中的形态 → 点名用 (族名, 原文)。"""
    covered = [m.span() for m in STD_COLLECT_PAT.finditer(text or "")]
    out = []
    for m in STD_LOOSE_PAT.finditer(text or ""):
        s, e = m.span()
        if any(a <= s and e <= b for a, b in covered):
            continue
        out.append(f"{(m.group(1) + (('/' + m.group(2)) if m.group(2) else '')).upper()}"
                   f" ← {m.group(0)!r}")
    return out


def unknown(printed: set[str], origins: set[str], exempt: set[str]) -> list[str]:
    """正向: 印出号里, 既不在源头也不在**能挡红的**登记项 → 未登记 (幽灵)。"""
    return sorted(pc for pc in printed if pc not in origins and pc not in exempt)


def zombies(exempt_hits: dict[str, int]) -> list[str]:
    """反向: 登记了豁免, 但在被扫报告里命中 0 次 → 僵尸条目。"""
    return sorted(c for c, n in exempt_hits.items() if n == 0)


# ── 1. 源头可读性 (读不到先短路, 不产出假绿) ──────────────────────────────────
print("### 1. 真实源头可读 (读不到必须点名, 不得静默变绿)")
_origins, _diag = load_origins()
check("一手目录快照 + standard_db 权威来源均已读到 (非空)", bool(_origins) and not _diag, _diag)
check("源头里含一手 GBZ 2.2-2007 (证明确实读进来了)", "GBZ2.2-2007" in _origins, True)
check("report_evidence 造的幽灵 GBZ2.2-2019 已从源头排除 (A 修复生效)",
      "GBZ2.2-2019" not in _origins, sorted(x for x in _origins if "2.2-2019" in x))

# ── 2. 书写形态归一 = 字符类/形态尺子, 不是同义词表 (M2/M8 变异: 能红的证据) ──────
print("\n### 2. 同一号的不同书写形态必须并成一个 (STD_SEPS 字符类 + 形态派生身份键)")
_forms = ["GBZ2.2-2007", "GBZ 2.2-2007", "GBZ 2.2—2007", "GBZ 2.2－2007", "gbz 2.2—2007"]
_norms = {norm_code(f) for f in _forms}
check("半角/全角/em/minus/大小写 五种写法 → 归一后是同一个号", _norms == {"GBZ2.2-2007"}, _norms)
check("归一口径来自单一尺子 norm_code (未另起同义词表)",
      getattr(norm_code, "__module__", "") == "knowledge.standard_version", norm_code.__module__)
_keys = {std_key(f) for f in _forms}
check("存在性口径 std_key 同样把五种写法并成一个", _keys == {"GBZ2.2-2007"}, _keys)
_bare = {std_key("GBZT189.9-2025"), std_key("GBZ/T 189.9—2025"), std_key("GBZ/T189.9-2025")}
check("无斜杠推荐性形态与带斜杠形态 → 同一个身份键 (#42 在采集面同样生效)",
      _bare == {"GBZ189.9-2025"}, _bare)
_m8 = {std_key(x) for x in ["HGT20698-2009", "HG/T 20698-2009", "HG/T20698—2009"]}
check("M8 化工行标三种写法 (无斜杠/带斜杠/全角破折号) → 同一个口径号",
      _m8 == {"HG20698-2009"}, _m8)


# ── 3. 变异用例: 每条被当证据的检查都要有一条"必然能红、每次都红"的反例 ──────────
print("\n### 3. 变异用例 (护栏必须能红)")
O, EX = _origins, load_exempt()
EXK = set(EX)                       # 全部登记项 (反向僵尸检查的被测对象)
EXH = honored(EX)                   # 能挡红的登记项 (一手已核实 ∪ 存量)
check("采集面族名单 ⊇ 改写面族名单 (两把尺子同源, 只在放宽处不同)",
      set(STD_FAMS) <= set(STD_COLLECT_FAMS), (STD_FAMS, STD_COLLECT_FAMS))
check("登记项按 status 分层 (一手已核实/待复核/存量), 且待复核不挡红",
      all(v["tier"] in TIERS for v in EX.values()) and
      {k for k, v in EX.items() if v["tier"] == "待复核"}.isdisjoint(EXH),
      sorted((k, v["tier"]) for k, v in EX.items()))

# M1 正向牙齿: 往一段"固定文本"注入一个目录里根本没有的号 → 必须被点名
m1_text = ("本项目评价依据: 《职业病危害评价通则》GBZ/T 277—2016、"
           "《某虚构标准》GBZ 9.9-9999。")
m1 = unknown(printed_codes(m1_text), O, EXK)
check("M1 注入 GBZ 9.9-9999 → 正向必点名", m1 == ["GBZ9.9-9999"], m1)

# M2 反向牙齿: 加一条从未被印出的豁免 → 僵尸必点名
m2_hits = {**{c: 1 for c in EXK}, "GBZ8.8-8888": 0}
check("M2 僵尸豁免 GBZ8.8-8888 (命中 0) → 反向必点名",
      zombies(m2_hits) == ["GBZ8.8-8888"], zombies(m2_hits))

# M3 A 的具体失败模式: 幽灵号即便换任意破折号写法仍不在源头 → 仍被红 (同义词表式豁免挡不住)
m3 = {p for p in ["GBZ2.2-2019", "GBZ 2.2—2019", "GBZ 2.2-2019"]}
m3 = unknown({std_key(p) for p in m3}, O, EXK)
check("M3 幽灵 2.2—2019 换写法仍是未登记 → 必点名 (证明不是靠同义词糊过去)",
      m3 == ["GBZ2.2-2019"], m3)

# M4 现行号不误伤: 已修复的 GBZ 2.2-2007 (一手现行) 不得被判未知
m4 = unknown({"GBZ2.2-2007"}, O, EXH)
check("M4 真号 GBZ 2.2-2007 (一手现行) 不误伤", m4 == [], m4)

# M5 源头失明的短路: 空源头时, 一个真号也会被判未知 —— 说明判据取的是数不是空集蒙混
m5 = unknown({"GBZ2.2-2007"}, set(), EXH)
check("M5 空源头(失明)时真号也被判未知 → 暴露'没测'而非'通过'", m5 == ["GBZ2.2-2007"], m5)

# M6 (#43 的红线): 登记 ≠ 放行 —— status='待复核' 的条目必须仍然红, 换成 '一手已核实' 才挡得住
_pend = "HG20571-2014"
_m6_ex = {_pend: {"code": "HG 20571-2014", "tier": "待复核", "reason": "一手未取到"}}
_m6_ok = {_pend: {"code": "HG 20571-2014", "tier": "一手已核实", "reason": "https://..."}}
check("M6 待复核登记项**不挡红** (拿豁免糊'护栏看不见'= 复刻幽灵号成因)",
      unknown({_pend}, O, honored(_m6_ex)) == [_pend], unknown({_pend}, O, honored(_m6_ex)))
check("M6 对照: 同一号一旦按'一手已核实'登记就退出红名单 (判据动作在 status, 不在号本身)",
      unknown({_pend}, O, honored(_m6_ok)) == [], unknown({_pend}, O, honored(_m6_ok)))
check("M6 前提对表: 真登记里确有 status='待复核' 的条目 (否则上面两条绿灯是空测)",
      any(v["tier"] == "待复核" for v in EX.values()), sorted(EX.items())[:3])

# M7 (#43 的结构性出口): 名单外的族一旦被印出, 必须出声, 不许再次"两把尺子都看不见"
_m7_text = "《某电力行业设计规程》DL/T 5053-2016、《某安全规程》AQ 3013-2008、" \
           "《化工企业安全卫生设计规范》HG/T 20571-2014。"
_m7 = offsurface_forms(_m7_text)
check("M7 采集面外的新族 (DL/AQ) → 盲区探测必点名, 已在采集面的 HG 不误伤",
      len(_m7) == 2 and all("HG" not in x for x in _m7), _m7)
check("M7 对照: 只有采集面内族时盲区探测为空 (名单内不误报)",
      offsurface_forms("按 GBZ 2.1—2019 与 HG/T 4334.1-2012 执行。") == [],
      offsurface_forms("按 GBZ 2.1—2019 与 HG/T 4334.1-2012 执行。"))


# ── 4. 现状扫描: 对真实成品逐段/逐格取印出号, 双向对账 ─────────────────────────
print("\n### 4. 现状扫描 (4 项目成品里印出的号: 正向未登记 + 反向僵尸 + 采集面盲区)")
ap = argparse.ArgumentParser()
ap.add_argument("--docx", default="/tmp/fp_docx")
ap.add_argument("--regen", action="store_true", help="现生成 4 项目成品再扫 (依赖导出链)")
ap.add_argument("--pids", default="ada54603a9,c8c7ff0a4d,2863a36451,57ffa2b3b0")
args, _ = ap.parse_known_args()

PIDS = [p for p in args.pids.split(",") if p]
printed_union: set[str] = set()
per_report: dict[str, list[str]] = {}
forms: dict[str, str] = {}
offsurf: list[str] = []
docx_dir = Path(args.docx)
gen_err = None

def _texts_from_docx(path):
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = Document(str(path))
    out = []
    for el in d.element.body:
        if el.tag == qn("w:p"):
            out.append(Paragraph(el, d).text)
        elif el.tag == qn("w:tbl"):
            for row in Table(el, d).rows:
                for c in row.cells:
                    out.append(c.text)
    return "\n".join(out)

try:
    if args.regen:
        from tools.export_fingerprint import export
        docx_dir.mkdir(parents=True, exist_ok=True)
        for pid in PIDS:
            _p = export(pid, docx_dir)   # 写 /tmp 临时 docx, 不动 golden
            t = _texts_from_docx(_p)
            per_report[pid] = printed_codes(t)
            forms.update(printed_raw_forms(t))
            one_off = offsurface_forms(t)
            offsurf += [f"{pid}: {x}" for x in one_off]
    else:
        for pid in PIDS:
            cand = next(iter(sorted(docx_dir.glob(f"*{pid}*.docx"))), None)
            if cand is None:
                gen_err = f"缺少 {pid} 的成品 docx (先跑 export_fingerprint --check 或加 --regen)"
                break
            t = _texts_from_docx(cand)
            per_report[pid] = printed_codes(t)
            forms.update(printed_raw_forms(t))
            one_off = offsurface_forms(t)
            offsurf += [f"{pid}: {x}" for x in one_off]
except Exception as e:                                # word_export 等依赖不可用 → 点名, 不伪装成绿
    gen_err = f"扫描成品失败 (导出链依赖不可用?): {type(e).__name__}: {e}"

if gen_err:
    check("现状扫描已执行 (未执行=不可信)", False, gen_err)
else:
    for pid, codes in per_report.items():
        printed_union |= codes
        u = unknown(codes, O, EXH)
        if u:
            print(f"    {pid}: 未登记 {[(x, forms.get(x, '?')) for x in u]}")
    # 命中计数 (反向对账): 每个豁免在被扫报告并集里命中几次
    hits = {}
    for c in EXK:
        hits[c] = sum(1 for codes in per_report.values() if c in codes)
    zom = zombies(hits)
    unk = unknown(printed_union, O, EXH)
    pend = [x for x in unk if x in {k for k, v in EX.items() if v["tier"] == "待复核"}]
    ghost = [x for x in unk if x not in pend]
    check("正向: 印出号全部能在 源头∪已核实登记 对上 (未登记=0; 待复核条目**刻意保持红**)",
          unk == [], f"待复核 {pend} / 疑似幽灵 {ghost}")
    check("反向: 每条豁免仍被 ≥1 份成品命中 (无僵尸条目)", zom == [], zom)
    check("采集面盲区: 成品里没有印出采集尺子看不见的族形态 (N1 的出口)",
          offsurf == [], offsurf)
    if pend:
        print("    ↑ 上面这些是 status='待复核' 的登记号: 一手出处没核到 → 按 #43 红线继续红, "
              "补到一手 URL 后改 status='一手已核实' 才退出。")
    if ghost:
        print("    ↑ 上面这些是**没有登记的号** (GBZ 1-2019 / GBZ/T 233-2019 那类): "
              "属授权范围外新发现, 转交处理")
    no_url = sorted(k for k, v in EX.items() if v["tier"] == "存量(未标 status)")
    if no_url:
        print(f"    · 存量登记项 {len(no_url)} 条没标 status/没留一手 URL (#43 前的口径), "
              f"本批不动它们, 下一批逐条补出处")

print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
