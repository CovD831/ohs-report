"""LLM 成文器 — 通用化 prompt 体系 (不写死项目数据)

原则:
  prompt = 通用结构要求 (任何项目适用) + 动态数据 (从项目/知识库注入)
  禁止: 写死某项目的具体数值/设备/工序 (换项目即错)
  动态数据注入: info 块 (项目名/行业/危害/设备/检测+OEL限值/原料)
  通用结构: 每个章节的"写法角度" (标准要求的角度, 跨项目通用)

数据来源 (全部动态):
  info.检测数据: 检测值 + 系统查 OEL 限值 (不做人工对照)
  info.原料: 原辅材料清单 (A2a 提取)
  info.工序: 工艺说明 (A2b 提取)
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge.project_assess import assess_project  # noqa: E402
from knowledge.oel import connect  # noqa: E402
from web.projects_db import get_project  # noqa: E402

# provider 统一: workbuddy2api 中转 (勿改回官方直连 — 全项目单一口径)
BASE = os.environ.get("LLM_BASE_URL", "https://workbuddy2api.henryai.top/v1/chat/completions")
MODEL = os.environ.get("LLM_MODEL", "deepseek-v4.1-flash")
KEY_ENV = os.environ.get("LLM_KEY_ENV", "DEEPSEEK_API_KEY")


# 全局叙述化指令 (system 固定, 所有章节生效)
_NARR_SYSTEM = (
    "你是职业病危害预评价报告撰写专家。请按正式评价报告的文体撰写段落。严格遵循："
    "1) 把给定的项目数据组织成连贯的叙述性段落，使用正式书面语；"
    "2) 禁止列出原始数据的标签、【】符号、括号标记或表格清单；"
    "3) 禁止把数据当作检查清单逐条罗列，而要写成完整的陈述句；"
    "4) 数据缺省的项用'待补充'或'依据标准推断'表达，不编造数值；"
    "5) 引用标准时写标准号全称（如 GBZ 2.1—2019），不写缩写。"
)


def _llm(prompt: str, system: str = _NARR_SYSTEM) -> str:
    """调用 LLM (workbuddy2api 中转, model=deepseek-v4.1-flash; key 从环境变量) — 3 次重试"""
    key = os.environ.get(KEY_ENV) or _load_env_key(KEY_ENV)
    if not key:
        raise RuntimeError(f"{KEY_ENV} 未配置")
    req = urllib.request.Request(BASE, data=json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": 0.3,
    }).encode(), headers={"Authorization": f"Bearer {key}",
                          "Content-Type": "application/json"})
    # 域名解析/网络偶发失败: 指数退避重试 (2s/4s/8s)
    # 读超时: 长章节(几千字) GLM 生成需 2-4 分钟, 60s 会超时→重试→再超时死循环(全部章节失败,
    # 任务卡 0%)。默认 300s, 可用 LLM_READ_TIMEOUT 覆盖。
    READ_TIMEOUT = int(os.environ.get("LLM_READ_TIMEOUT", "300"))
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=READ_TIMEOUT) as r:
                d = json.loads(r.read())
            return d["choices"][0]["message"]["content"]
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"LLM调用失败(3次): {last_err}")


def _load_env_key(name: str) -> str:
    """从项目根 .env 读 key (部署机) — 本机开发回退 hermes .env"""
    for cand in (Path(__file__).resolve().parent.parent / ".env",
                 Path("/Users/abaaba/.hermes/.env")):
        if cand.exists():
            for ln in cand.read_text().splitlines():
                if ln.startswith(name + "="):
                    return ln.split("=", 1)[1].strip()
    return ""


def _build_info(project: dict, assess: dict) -> str:
    """动态信息块 — 叙述化 (非占位符), 让 LLM 直接引用事实而非照搬标签

    把 机械数据(危害/设备/工序/检测+限值/原料/风险) 组织成正式叙述句,
    避免 LLM 照搬【工序】【设备】等标签。
    """
    conn = connect()
    chain = assess.get("industry_chain") or {}
    risk = assess.get("industry_risk") or {}
    hazards = assess.get("hazards", [])
    eqs = project.get("equipment", [])[:20]
    # 危害因素过滤: 只保留能回溯到项目数据的因子 (物料→因子/检测因子/grid因子)
    # (修复: 报告出现'硫酸二甲酯/甲苯' — 项目data无此物料, assess从库/行业模板带入 → 混入清单)
    _proj_mats = set()
    for m in (project.get("materials") or []):
        nm = str(m.get("name") or m if isinstance(m, dict) else str(m)).split("|")[0].strip()
        if nm:
            _proj_mats.add(nm)
    _proj_grid_factors = set()
    for g in (project.get("hazard_grid") or []):
        if isinstance(g, dict):
            for f in str(g.get("factors") or "").replace(" ", "").split("、"): 
                if f and len(f) >= 2: _proj_grid_factors.add(f)
    _det_factors = set(str(d.get("factor") or "") for d in (project.get("detections") or []))
    # 通用物理/粉尘因子 (不限物料: 噪声/高温/振动/粉尘等若grid/检测出现则保留; 无来源不入列表)
    hz_keep_common = {"噪声", "高温", "局部振动", "工频电场", "紫外辐射", "矽尘", "电焊烟尘",
                      "其他粉尘", "粉尘", "噪声(等效声级)", "WBGT"}
    def _mats_backed(factor):
        """因子能否回溯到项目物料: 精确相等(如'硫酸'=='硫酸') 或 因子是某物料名的完整后缀/全名
        (禁用宽松子串: '硫酸'会匹配'硫酸二甲酯'/'甲苯'匹配'N,N-二甲基对甲苯胺' → 混入不在物料清单的因子)"""
        for k in _proj_mats:
            if not k:
                continue
            if factor == k:
                return True
            # 因子是物料名的完整结尾/开头 (物料'硫酸'→因子'硫酸'; 物料'五氧化二磷'→因子'磷'? 不保留)
        return False
    hazards = [h for h in hazards
               if h.get("factor") in _det_factors
               or _mats_backed(h.get("factor"))
               or h.get("factor") in _proj_grid_factors
               or h.get("factor") in hz_keep_common]

    # 危害因素 → 叙述
    hz_parts = []
    for h in hazards[:15]:
        src = "、".join((h.get("sources") or [])[:2])
        hz_parts.append(f"{h['factor']}" + (f"（主要来自{src}）" if src else ""))
    hz_str = "、".join(hz_parts) if hz_parts else "本项目原料、中间产物及工艺过程中暂无明显职业病危害因素（需待材料补齐后识别）。"

    # 检测数据 → 叙述 (含 OEL 限值对照)
    det_parts = []
    for d in project.get("detections", [])[:15]:
        fname = d.get("factor", "")
        val = d.get("ctwa")
        limit = None
        for r in conn.execute(
                "SELECT oel_type, value, unit FROM oel_limit WHERE factor_name LIKE ? LIMIT 1",
                (fname + "%",)):
            limit = f"{r[0]} {r[1]} {r[2]}"
            break
        val_str = f"{val} mg/m³" if val is not None else "未检出"
        det_parts.append(f"{fname}检出{val_str}" + (f"，其职业接触限值(PC-TWA)为{limit}" if limit else "，限值待查GBZ 2.1"))
    det_str = "；".join(det_parts) if det_parts else "本项目暂无类比检测数据，接触水平依据GBZ 2.1—2019推断。 "
    # 检测部分避免'限值:'标签
    det_str = det_str.replace("限值:", "")

    # 工序 → 叙述
    procs = [ln.strip().lstrip("- ") for ln in (project.get("process_text") or "").splitlines()
             if ln.startswith("- 车间")][:12]
    proc_str = "。".join(p.replace(" | 工序 ", "生产工序为").replace(" | 密闭 ", "，密闭方式为")
                          .replace(" | 危害 ", "，主要危害为") for p in procs)
    if proc_str:
        proc_str = "主要生产工序包括：" + proc_str + "。"
    else:
        proc_str = "本项目生产工艺流程描述待补充。 "

    # 设备 → 叙述
    eq_str = "、".join(str(e).split("|")[0] for e in eqs[:12]) or "无"
    eq_desc = f"项目主要设备包括：{eq_str}。" if eq_str else "项目设备清单待补充。 "

    # 原料 → 叙述
    mats = []
    f = Path(__file__).resolve().parent.parent / "data" / "materials" / "A2a_原辅材料清单.csv"
    if f.exists():
        import csv
        with open(f, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                n = (r.get("名称") or "").strip()
                if n:
                    mats.append(n)
    mat_str = "、".join(mats[:15]) or "无"
    mat_desc = f"主要原辅材料为：{mat_str}。" if mat_str else "原辅材料清单待补充。 "
    conn.close()

    # 汇总成叙述化信息块 (每项独立成段, 无【】标签)
    return (f"一、项目概况：{project.get('name', '未命名项目')}，所属行业{chain.get('mid', {}).get('name', '—')}（{chain.get('full', '—')}），"
            f"该项目属于{risk.get('level', '—')}职业病危害风险类别。\n"
            f"二、主要职业病危害因素：{hz_str}。\n"
            f"三、检测与接触水平：{det_str}。\n"
            f"四、{eq_desc}\n"
            f"五、{proc_str}\n"
            f"六、{mat_desc}\n"
            f"以上数据为系统从企业材料中机械提取与判定，部分限值未列出的按GBZ 2.1—2019执行。")


# ===== 通用章节 prompt (结构=标准角度, 数据=info动态) =====
# 质量导向的通用写作指令 (替代裸字数限制: 不靠"写多少字", 靠信息密度标准)
_WRITING_GUIDE = """
【写作标准】(硬性)
1. 信息密度优先: 每句落实一个数据/事实, 只写信息块里有的, 不空泛铺垫
2. 直接引用数据: 用具体名称/数值(如"氩弧焊机""矽尘""GBZ 2.1"), 不写"多种""某类"等模糊词
3. 去套话: 禁用"随着…发展""综上所述""本报告认为""近年来"等空话
4. 不重复: 信息块已列出的内容(设备清单/危害因素表)不逐条复述, 只提炼"该环节的风险特征"
5. 数据缺失=明确标注: 信息块没有的值(如投资/规模)写"待补充(需企业提供)", 不编造不展开
5b. 物料/危害因素/设备清单严格限于信息块: 信息块列出的才可写; 信息块外(如"硫酸二甲酯/甲苯"
    在项目无此物料)一律不得出现 — 严禁从标准库/常识/其他行业知识补料, 违反=重大差错
5c. 行业/项目属性(属哪个行业/风险类别/性质/规模)必须照抄信息块, 绝不自行改写或联想
    (如信息块"2651初级形态塑料及合成树脂制造" → 不得写成"信息传输、软件和信息技术服务业")
5d. 标准条款限值(氧含量/浓度限值/温度等)必须以信息块给的数值为准, 不得凭记忆/其他标准改写
    (如信息块"氧含量19.5%~21%(GB 30871—2022)" → 不得写成"18%~22%")
5d-2. **标准号必须逐字符照抄信息块, 不得凭记忆写**: 前缀(GB/GB/T/GBZ/GBZ/T 不可互换)、
    空格、年份全部照抄。实测错法(均为重大差错):
      ✗ GBZ50019-2015  (实为 GB 50019-2015, 多写 Z)
      ✗ GB/T 2890—2022 (实为 GB 2890—2022, 强制性标准误写成推荐性)
      ✗ GB/T196-2007   (实为 GBZ/T 196—2025, 既写错前缀又引了废止版)
    信息块里没给的标准号**一律不写**; 确需引用外部标准时只写标准**名称**, 不写编号。
5e. 企业背景/资质/荣誉类信息一律以信息块为唯一来源: 认证历程(BV/ISO/清洁生产/安全标准化/其他奖项)、
    集团经验年限、产品应用领域、市场背景/供需描述、产线历史 — 信息块没给的一律不写, 写"待补充(需企业提供)",
    严禁凭行业常识/模型记忆/原报告印象补写 (违反=重大差错)
5f. **严禁在正文里写表格**(Markdown 竖线表/制表符表/逐行罗列明细): 表格由系统自动插入到对应位置。
    正文只写文字; 需要引用数据时统一用"见表X.X-X"指代, 不复制表格内容到正文
    (违反=重复+格式错乱: 正文里出现"| 序号 | 名称 |..."这类内容一律禁止)
    ⚠ **也不要在正文里写表题占位**: 表题(如"表5.3-1 XXX")+表格本体都由系统在对应位置
       自动插入。正文里出现"表X.X-X ..."字样会与系统插入的表题**重号重复**。
       ✗ 反例(实测): 正文写"表 5.3-1 本项目…一览表（表格由系统自动插入）"
         → 系统又插一条"表5.3-1 化学有害因素的接触限值" → **同一编号出现两次**
       正确做法: 只写"…见表5.3-1。"(正文内指代), **不要另起一行写表题**
5g. **严禁用句子复述表格数据**(本条是 5f 的补强: 不写表格 ≠ 可以逐条念表格):
    已做成表格的内容 — 各因素的**浓度值/接触限值/PC-TWA/判定结论** — 正文不得逐条重述。
    ✗ 反例(逐条念表, 实测一段能到 2000 字):
       "苯乙烯接触水平低于1.7 mg/m³，低于GBZ 2.1—2019规定的PC-TWA 50 mg/m³；
        乙二醇接触水平低于0.7 mg/m³，低于…PC-TWA 20 mg/m³；甲醇…"(同一句式重复 N 次)
    ✓ 正例(提炼判断 + 指代表格):
       "各化学因素接触水平均低于GBZ 2.1—2019限值要求(见表5.3-1)，其中苯乙烯、乙二醇、
        甲醇等主要因素检出值均在限值的10%以下。"
    判据: 正文出现"<因素名>接触水平…低于/符合…限值"句式**连续 >=3 次**即违规。
    **保留**: 因素名称清单(专家需要看涉及哪些因素)、关键项的定性判断、超标项(若有)必须单独点明。
    **禁止**: 逐条罗列每个因素的浓度数值与对应限值(表格里已有, 重复=冗余)。
5h. **汇总型章节禁止重述前章明细**: 结论/补充建议/关键控制点等汇总章节只写
    归纳结论与判断, 不把前面各章的表格数据再念一遍(引用写"详见表X.X-X/第X章")。
6. 结构紧凑: 短句, 一段一个主题, 同一信息不换个说法重说
7. 每节一屏读完: 正文不超过信息块的1.5倍长, 信息块短则正文短, 不为凑篇幅铺垫
8. 章节边界(防串章): 只写本节主题, 以下内容已在别的节写过, 本节禁止复述 —
   项目地址/投资/规模/风险类别 → 只在1.1/3.1写; 评价目的 → 1.2; 评价依据条款 → 1.3;
   工艺流程 → 3.5; 危害因素清单 → 5.1; 防护设施明细 → 6.1/6.2; 应急 → 7.1/7.2;
   PPE配置 → 8; 管理 → 9; 监护 → 10.5。引用别的节写"详见X.X"即可, 不重述内容
8b. 跨章引用必须指向正确章节: 防护设施→第6章, 应急救援→第7章, PPE→第8章;
    (禁错引: 防护设施章节不得写"详见7.1"那是应急救援)
"""


def _design_base() -> str:
    """标准措施条款摘要 (系统查询, 供建议类prompt)"""
    conn = connect()
    lines = []
    for r in conn.execute("SELECT check_point, std_code, clause FROM protection_rule"):
        lines.append(f"- [{r[1]} {r[2]}] {r[0][:40]}")
    for r in conn.execute("SELECT require, std_code, clause FROM management_rule"):
        lines.append(f"- [{r[1]} {r[2]}] {r[0][:40]}")
    conn.close()
    return "\n".join(l for l in lines if l)[:1500]


def build_section_prompt(sec: str, info: str) -> str:
    """按章节返回通用 prompt (无写死数据, 结构跨项目)"""
    DESIGN_BASE = _design_base()
    prompts = {
        "10.2.1": f"""撰写 10.2.1 总论的叙述。

{info}

要求:
1. 评价目的: 识别预测拟建项目职业病危害, 提出防治措施
2. 评价依据: 职业病防治法+GBZ标准体系(详见评价依据表, 标准现行版由系统核验)
3. 评价范围: 投产运行期间主要危害因素及防治
4. 评价方法: 工程分析法/类比法/检查表法
5. 正式报告语言, 250-350字""",

        "10.2.2": f"""撰写 10.2.2 现有企业概况(改扩建项目)的叙述。

{info}

要求:
1. 现有企业建厂时间/行业/规模/人数(字段表见采集表, 数据待用户填)
2. 现有职业病危害现状概述
3. 职业卫生机构/制度/监护现状
4. 正式报告语言, 200-300字""",

        "10.2.3.7": f"""撰写 10.2.3.7 建筑卫生学分析与评价的叙述。

{info}

要求 (通用角度):
1. 选址与总体布局: 产生危害车间布置/功能分区 (GBZ 1—2010 5.1/5.2.1)
2. 建筑卫生学: 车间卫生特征等级/采光(GB/T 50033)/照度(GB/T 50034-2024)
3. 辅助用室: 浴室/更衣室/盥洗 配置 (GBZ 1—2010 表10/表11 卫生特征1-4级)
4. 暖通: 自然通风/机械通风 (GB 50019)
5. 结合本项目实际危害因素判断适用条款
6. 正式报告语言, 300-450字""",

        "10.2.4": f"""撰写 10.2.4 类比企业(项目)调查与分析。

{info}

要求:
1. 类比项目选择原则 (同行业/同原料/同工艺优先)
2. 九要素可比性: 自然环境/原料/规模/定员/制度/工艺/设备/防护/管理 (附录C)
3. 类比结论: 可比/基本可比/不可比 及依据
4. 正式报告语言, 250-350字""",

        "10.2.7": f"""撰写 10.2.7 应急救援措施的分析与评价。

{info}

要求:
1. 应急救援组织机构及人员配置
2. 应急救援设施: 应急撤离通道/泄险区/现场急救用品/冲洗设备/报警装置/事故通风/风向标 (GBZ 1、GBZ/T 223、GBZ/T 205、GB/T 38144)
3. 应急救援制度与预案: 针对本项目可能发生急性中毒/灼伤/窒息的危害因素
4. 用【应急救援】【危害因素】数据动态描述, 缺数据标'待补充'
5. 腐蚀性介质场所须述防腐蚀措施, 依据《工业建筑防腐蚀设计规范》GB 50046-2008
   (罐区地面/设备基础防腐蚀, 腐蚀性介质管道选材; 无材质/除锈等级数据则只写措施依据不编具体参数)
6. 评价应急救援措施的符合性和合理性
7. 正式报告语言, 300-450字""",

        "10.2.8": f"""撰写 10.2.8 个人使用的职业病防护用品的分析与评价。

{info}

要求:
1. 按工种(岗位)×接触的危害因素列出PPE (防尘/防毒/防噪声/防振/防暑/防寒/防辐射等)
2. 依据 GB 39800、GB/T 18664、GB/T 23466、GBZ/T 195 评价符合性
   ⚠ 引用标准**只能照抄下方【标准版本基准】给出的完整号**（含年份），一个字都不许改。
     该表没有列出的标准就**只写标准名、不写年份**，或干脆不引 ——
     **严禁凭记忆补年份**（实测错法：GB/T 18664 → 补成废止的 2002 版，应为 GB 18664-2025）
3. 从【个人防护用品】【危害因素】【岗位定员】动态取数据
4. 评价佩戴的符合性和合理性
5. 正式报告语言, 250-400字""",

        "10.2.9": f"""撰写 10.2.9 职业卫生管理的分析与评价。

{info}

要求:
1. 职业卫生管理机构与人员配置
2. 职业病防治计划/管理制度/操作规程/档案/告知/警示标识
3. 职业健康监护/危害因素监测检测/应急救援预案/培训
4. 职业病防治专项经费概算 (GBZ 1、GBZ 158、GBZ 188、GBZ/T 225)
5. 评价职业卫生管理措施的符合性
6. 正式报告语言, 300-450字""",

        "10.2.10": f"""撰写 10.2.10 职业病危害关键控制点分析的叙述。
相关标准措施条款（建议措施引用这些条款）:
{DESIGN_BASE}

{info}

要求 (通用: 岗位×危害×措施):
1. 按岗位(或工序)列关键控制因子 (从【工序】【危害因素】判断)
2. 每个控制点: 控制级别(★★★/★★/★) + 控制措施(引用上述标准条款)
3. 关键控制点判定: 危害程度×接触人数×防护水平
4. 正式报告语言, 350-550字""",

        "10.2.11": f"""撰写 10.2.11 职业病防治措施的补充建议。
相关标准措施条款（系统从标准库查询，建议必须引用这些条款）:
{DESIGN_BASE}

{info}

要求:
1. 每条建议引用上述标准条款（标准号+条款号）
2. 六项通用建议: "三同时"/监测检测/健康监护/警示标识/培训/经费
3. 结合【检测数据】超标或接近限值的因素重点建议
4. 建议具体可执行（措施+依据）
5. 正式报告语言, 400-600字""",

        "10.2.12": f"""撰写 10.2.12 结论与建议的叙述 (报告结论章, 定式写法)。

{info}

要求(按真实报告结论章结构, 逐段写全):
1. 开篇总述: "本次预评价对[项目名称]存在的主要职业病危害因素、拟采取的职业病危害防护措施等进行了识别、分析和评价，评价结论如下："
2. 风险类别判定: 按《国民经济行业分类》(GB/T 4754) 所属行业 + 《建设项目职业病危害风险分类管理目录》, 结合工艺/物料/设备/过程控制, 判定风险类别(严重/较重/一般)及依据
3. 关键控制点与主要评价因子: 按工序列出关键控制点 + 对应主要评价因子(物质名, 用信息块给的)
4. 达标结论: "通过工程分析、职业病危害因素识别、类比企业调查及检测，在正常运行情况下，操作人员如能严格按照操作规程，作业场所各危害因素浓度应能达到《工作场所有害因素职业接触限值》的要求。"
5. 合规性评价: 拟采取的职业卫生防护措施基本合理, 符合《职业病防治法》《工业企业设计卫生标准》等要求, 不足部分已在报告中提出, 建议施工设计阶段补充完善
6. 可行性结论: 防护措施与补充建议一并实施后, 预计竣工投产后工作场所职业病危害可得到有效预防和控制, 从职业病防治角度分析项目可行
7. 不得编造: 风险类别/关键控制点/因子只能来自信息块; 缺失写待补充(需企业提供)""",
    }
    return prompts.get(sec, "")


def build_1023_prompt(project: dict, assess: dict, info: str) -> str:
    """10.2.3 工程分析 (通用结构, 数据动态)"""
    chain = assess.get("industry_chain") or {}
    hazards = [h["factor"] for h in assess.get("hazards", [])]
    eqs = project.get("equipment", [])[:25]
    return f"""你是职业病危害预评价报告撰写专家。撰写 10.2.3 建设项目工程分析的正式叙述。

{info}

要求 (通用结构, 数据从【设备】【工序】【原辅材料】动态取):
1. 基本情况: 项目名称/性质/生产规模/地点
2. 工程内容: 主要生产装置/辅助公用工程
3. 生产工艺流程: 按【工序】逐段简述
4. 主要职业病危害产生环节 (设备/工序→危害)
5. 不得编造数据, 只用信息块内容
6. 400-600字"""


def build_1025_prompt(assess: dict, info: str) -> str:
    """10.2.5 危害分析 (通用三角度, 数据动态)"""
    return f"""你是职业卫生评价专家。撰写 10.2.5 职业病危害因素识别及危害程度分析的叙述。

{info}

要求 (通用结构):
1. 生产工艺过程识别: 按【工序】逐段 → 化学因素 (从【危害因素】取)
2. 生产环境识别: 照明/微气候/通风不良 (如项目有)
3. 劳动过程识别: 体力劳动强度/人机工效 (如项目有)
4. 施工过程识别: 基坑/高处/焊接 (如项目有)
5. 健康影响: 各因素可能导致的职业病
6. 只写数据支持的内容, 不编造; 500-800字"""


def build_1026_prompt(info: str) -> str:
    """10.2.6 防护设施 (通用四层结构, 数据从检测信息动态度评价)"""
    return f"""撰写 10.2.6 职业病防护设施分析与评价的叙述。

{info}

要求 (通用四层结构):
1. 【防毒设施】:
   - 工艺控制: 密闭化/管道化/自动化程度 (从【设备】【工序】判断)
   - 局部排风: 排毒点设排风罩(GB/T 16758)/排毒系统(GBZ/T 194 3.8)
   - 物料防护: 设备密闭/投料控制
2. 【防尘设施】: 湿式作业/除尘(GBZ 1—2010 7.1.2), 投料防逸散 (如项目有粉尘)
3. 【防噪声设施】: 隔声/消声/减振(GB/T 50087) (如项目有高噪设备)
4. 【防高温设施】: 隔热/通风/降温 (如项目有热源)
5. 【效果评价】: 用【检测数据(含OEL限值)】对照 GBZ 2.1 限值定量判断
   (每个有检测值的因素: 数值对比限值→低于OEL则措施有效)
6. 只写信息块支撑的内容(设备/检测/工序), 不编造项目特性
7. 正式报告语言, 500-800字"""


def build_sub_prompt(sub: str, info: str) -> str:
    """二级小节通用 prompt (小节号 → 写法角度, 数据动态, 适配11章平铺)"""
    tmpl = {
        # 第1章 总论
        "1.1": "撰写 1.1 项目背景: 项目由来/立项意义/产品背景/产业政策(从【项目名称】【行业】判断)",
        "1.2": "撰写 1.2 评价目的: 识别预测职业病危害/提出防治措施/分类管理依据",
        "1.3": "撰写 1.3 评价依据: 法律、法规和规章 + 技术规范和标准 + 基础技术资料 (职业病防治法+GBZ体系)",
        "1.4": "撰写 1.4 评价范围: 投产运行期间主要职业病危害因素及防治措施",
        "1.5": "撰写 1.5 评价内容: 工程分析/危害识别/防护措施/管理/结论",
        "1.6": "撰写 1.6 评价方法: 工程分析法/类比法/检查表法 (GBZ/T 196 规定方法)",
        "1.7": "撰写 1.7 评价程序: 准备→工程分析→类比→检测→评价→报告",
        "1.8": "撰写 1.8 质量控制: 资料审核/检测资质/三级审核",
        # 第2章 现有企业概况
        "2.1": "撰写 2.1 现有企业概况: 建厂时间/所属行业/规模/职工人数/生产工人数/接触危害人数(改扩建项目)",
        "2.2": "撰写 2.2 职业卫生管理情况: 机构/人员/制度/操作规程/监护情况/职业病发病处置",
        "2.3": "撰写 2.3 职业病危害防护设施: 现有防尘防毒防噪防高温设施及运行情况",
        "2.4": "撰写 2.4 个人防护用品配备情况: 现有岗位PPE配备/佩戴情况",
        "2.5": "撰写 2.5 卫生辅助用室设置情况: 浴室/更衣室/盥洗/休息室设置",
        # 第3章 建设项目工程分析
        "3.1": "撰写 3.1 工程概况分析: 项目名称/性质/规模/地点/环境/项目组成/生产制度/定员/技术经济指标",
        "3.2": "撰写 3.2 选址分析与评价: 项目方位/周边企业/距离, 依据GBZ 1 5.1评价; "
               "自然条件若含地勘数据(地震动峰值加速度/设计地震分组/场地土类型/场地类别)"
               "则按GB 50011-2010判定场地类别与抗震地段属性, 无则如实标注「以地勘报告为准」",
        "3.3": "撰写 3.3 总体布局分析与评价: 总平面布置/竖向布置/功能分区(GBZ 1 5.2)",
        "3.4": "撰写 3.4 产品及原辅材料分析: 产品方案/规模 + 原辅材料清单(【原辅材料】)+形态用量",
        "3.5": "撰写 3.5 生产工艺分析与评价: 逐产品/工段简述工艺(【工序】)+产污环节",
        "3.6": "撰写 3.6 生产设备及布局分析与评价: 主要设备(【设备】)+布局密闭化自动化程度",
        "3.7": "撰写 3.7 建筑卫生学分析与评价: 建筑结构/通风空调/采光照明(GB/T 50033/50034)",
        "3.8": "撰写 3.8 辅助用室分析与评价: 浴室/更衣室/盥洗配置(GBZ 1 表10/11 卫生特征等级)",
        # 第4章 类比调查
        "4.1": "撰写 4.1 类比企业的选择: 同行业/同原料/同工艺类比原则(GBZ/T 196 附录C)",
        "4.2": "撰写 4.2 类比企业职业卫生调查: 工作日写实/危害因素分布/防护设施/PPE/应急救援",
        "4.3": "撰写 4.3 类比企业职业卫生管理: 机构/人员/制度情况",
        "4.4": "撰写 4.4 类比企业职业病危害因素检测: 类比检测值对照OEL(【检测数据】)",
        "4.5": "撰写 4.5 类比企业职业健康监护: 检查项目/周期/异常情况",
        "4.6": "撰写 4.6 类比调查综合结论: 九要素可比性(可比/基本可比/不可比)",
        # 第5章 危害分析
        "5.1": "撰写 5.1 职业病危害因素识别: 按工艺/环境/劳动过程识别, 用【危害因素】【工序】【原辅材料】",
        "5.2": "撰写 5.2 职业病危害因素健康效应分析: 理化性质/侵入途径/健康影响/可能职业病",
        "5.3": "撰写 5.3 职业接触限值: 化学因素PC-TWA/PC-STEL/PC-MAC + 物理因素限值(GBZ 2.1/2.2)",
        "5.4": "撰写 5.4 职业病危害因素危害程度分析: 预期接触水平对照限值/预测/异常情况/关键控制点",
        # 第6章 防护设施
        "6.1": "撰写 6.1 职业病防护设施分析: 拟采取防尘/防毒/防噪声振动/防高温设施(按评价单元)",
        "6.2": "撰写 6.2 职业病防护设施评价: 依据GBZ 1/GBZ/T 194评价符合性和合理性",
        # 第7章 应急救援
        "7.1": "撰写 7.1 应急救援措施分析: 组织机构/设施(喷淋洗眼GB/T 38144/报警/通风)/预案",
        "7.2": "撰写 7.2 应急救援措施评价: 依据GBZ 1/GBZ/T 205评价符合性",
        # 第8章 PPE
        "8.1": "撰写 8.1 个人使用的职业病防护用品分析: 按岗位×危害列出PPE(GB 39800)",
        "8.2": "撰写 8.2 个人使用的职业病防护用品评价: 依据GB 39800/GBZ/T 195评价符合性",
        # 第9章 职业卫生管理
        "9.1": "撰写 9.1 职业卫生管理分析与评价: 机构/制度/档案/监护/警示标识(GBZ/T 225)",
        "9.2": "撰写 9.2 职业卫生专项投资分析与评价: 防护设施/检测/应急/PPE/体检/培训经费概算",
        # 第10章 关键控制点 (标准 10.2.10, v46 新增章)
        "10.1": "撰写 10.1 职业病危害关键控制点: 按危害程度/接触人数/防护水平筛选关键控制因子×工序, 明确控制级别",
        "10.2": "撰写 10.2 关键控制措施: 针对关键控制点提出工程防护/个体防护/应急管理措施",
        # ⚠ v46 章号平移后必须同步: 原 10.x(补充建议) → 现 11.x; 原 11.1(结论) → 现 12.1
        #   旧表未改导致 11.2-11.7/12.1 无模板 → 返回"暂不支持该小节" → 8 个单元静默缺失
        # 第11章 补充建议 (标准 10.2.11)
        "11.1": "撰写 11.1 职业卫生'三同时': 防护设施与主体工程同时设计/施工/投产",
        "11.2": "撰写 11.2 补充措施及建议: 各危害因素针对性措施(条款驱动)",
        "11.3": "撰写 11.3 职业病危害因素接触者个人培训与防护: 岗前/在岗培训+个体防护教育",
        "11.4": "撰写 11.4 工作场所职业病危害警示标识: 危害岗位标识(GBZ 158)/中文说明",
        "11.5": "撰写 11.5 职业健康监护: 按危害因素的检查项目/周期(GBZ 188)",
        "11.6": "撰写 11.6 受限空间作业措施: 氧18-22%/可燃<10%LEL/审批监护(GBZ/T 205)",
        "11.7": "撰写 11.7 应急救援措施: 预案/设施/演练补充建议",
        # 第12章 结论 (标准 10.2.12)
        "12.1": "撰写 12.1 评价结论: 危害类别判定/主要问题/可行性结论",
    }
    tpl = tmpl.get(sub, "")
    if not tpl:
        return ""
    return f"""你是职业卫生评价专家。{tpl}。

{info}

要求:
1. 只写信息块支撑的内容, 数据动态(检测值/限值/设备/工序来自项目)
2. 引用标准条款(如GBZ/T 194/GBZ 1/GB/T 50034等)
3. 正式报告语言, 150-300字"""


def _assess_cached(pid: str, _cache: dict | None = None):
    """计算一次并复用 assess + info (避免每单元重复计算)"""
    if _cache is not None and _cache.get("assess") is not None:
        return _cache
    p = get_project(pid)
    conn = connect()
    d = p["data"]
    project = {"name": p["name"], "industry": d.get("industry", ""),
               "equipment": d.get("equipment", []),
               "detections": d.get("detections", []),
               "process_text": d.get("process_text", ""),
               "materials": d.get("materials", []),
               "staffing": d.get("staffing", []),
               "protection": d.get("protection", ""),
               "ppe": d.get("ppe", []),
               "emergency": d.get("emergency", ""),
               "investment": d.get("investment", ""),
               "capacity": d.get("capacity", ""),
               "area": d.get("area", ""),
               "nature": d.get("nature", ""),
               "location": d.get("location", ""),
               "buildings": d.get("buildings", []),
               "facilities": d.get("facilities", []),
               "products": d.get("products", []),
               "equipment_detail": d.get("equipment_detail", []),
               "shifts": d.get("shifts", []),
               "company": d.get("company", ""), "founded": d.get("founded", ""),
               "registered_capital": d.get("registered_capital", ""),
               "legal_rep": d.get("legal_rep", ""), "investor": d.get("investor", ""),
               "hazard_grid": d.get("hazard_grid", []),
               "emergency_supplies": d.get("emergency_supplies", []),
               "profile": d.get("profile", {}),
               "built_tables": d.get("built_tables", {}), "location": d.get("location", "")}
    assess = assess_project(conn, project)
    conn.close()
    info = _build_info(project, assess)
    return {"project": project, "assess": assess, "info": info}


# 固定文本章节 (GBZ/T 196 定式写法 — LLM 零参与, 防乱说话/编造)
# key=节号, value=最终正文. 动态部分用 {proj}/{industry} 占位, 由 _proj_fill 替换
FIXED_TEXTS = {
    "1.2": (
        "（1）贯彻落实《中华人民共和国职业病防治法》及国家相关的法律、法规、规章、标准和产业政策，"
        "从源头控制和消除职业病危害，防治职业病，保护劳动者健康。\n"
        "（2）识别、分析{proj}可能产生的职业病危害因素，评价其危害程度，确定职业病危害类别，"
        "为建设项目职业病危害分类管理提供科学依据。\n"
        "（3）在对职业病危害因素识别和分析的基础上，论证该建设项目拟采取的职业病危害控制措施的"
        "可行性、有效性及合理性，提出相应的补充措施，以完善职业病防治的对策，使该建设项目建成投产后"
        "能符合国家有关职业卫生法律、法规、标准和规范的要求。\n"
        "（4）从职业病防治角度评估建设项目的可行性，为本项目的初步设计提供职业病危害预防的技术依据。"
    ),
    "1.3.1": (
        "本项目的评价依据主要包括以下法律、法规、规章：\n"
        "（1）《中华人民共和国职业病防治法》（主席令第24号，2018年修正）；\n"
        "（2）《中华人民共和国劳动法》；\n"
        "（3）《中华人民共和国安全生产法》；\n"
        "（4）《中华人民共和国清洁生产促进法》；\n"
        "（5）《工作场所职业卫生管理规定》（国家卫生健康委令第5号）；\n"
        "（6）《建设项目职业病防护设施\u201c三同时\u201d监督管理办法》（国家安全生产监督管理总局令第90号）。\n"
        "以上法规现行有效版本为评价依据。"
    ),
    "1.3.2": (
        "本项目的评价依据主要包括以下技术规范和标准（现行有效版本，详见评价依据标准清单表）：\n"
        "（1）基础标准：《工业企业设计卫生标准》（GBZ 1—2010）、《工作场所有害因素职业接触限值 "
        "第1部分：化学有害因素》（GBZ 2.1—2019）、《工作场所有害因素职业接触限值 第2部分：物理因素》（GBZ 2.2—2019）；\n"
        "（2）评价导则：《建设项目职业病危害评价通则》（GBZ/T 277）、《建设项目职业病危害预评价技术标准》（GBZ/T 196—2025）；\n"
        "（3）专项标准：职业病危害因素检测与评价（GBZ/T 160 系列工作场所空气有毒物质测定）、"
        "职业健康监护技术规范（GBZ 188—2025）、警示标识（GBZ 158）、防护设施设计与检测（GB/T 16758 排风罩、"
        "GB/T 50087 噪声控制、GB/T 50493 检测报警设计）等。\n"
        "评价中涉及的具体限值与检测方法标准，以评价依据表中现行有效版本为准。"
    ),
    "1.3.3": (
        "本评价的基础技术资料由建设单位提供，主要包括：\n"
        "（1）项目申请报告（可行性研究）；\n"
        "（2）现状评价报告及企业职业卫生管理资料；\n"
        "（3）类比对标企业职业病危害因素检测报告；\n"
        "（4）项目总平面布置图及设备清单等工程设计资料。\n"
        "上述资料的真实性、完整性由建设单位负责。资料如有缺失，相关数据在报告中如实标注\u201c待补充（需企业提供）\u201d。"
    ),
    # ── 3.9 建设施工工程分析 (定式: 标准施工工序流程, 与项目无关 — 真稿8.7全量取证) ──
    # 零 LLM: 施工工艺流程是规范化的通用工序描述, 不含项目特有数据, 不应由 LLM 生成
    "3.9": (
        "本项目施工过程主要包括基础工程、模板工程、钢筋工程、混凝土工程、砌体工程、"
        "抹灰工程、楼地面工程及饰件工程等，各分项工程的施工工艺流程如下。"
    ),
    "3.9.1": (
        "基础工程施工工艺流程：定位放线→复核（包括轴线，方向）→桩机就位→打桩→测桩→"
        "基槽开挖→锯桩→浇筑砼垫层→轴线引设→承台模板及梁底板安装→钢筋制安→"
        "承台模板及基础梁侧板安装→基础模板、钢筋验收→浇筑基础砼→养护→基础砖砌筑→回填土。"
    ),
    "3.9.2": (
        "模板工程施工工艺流程：轴线投设→柱（剪力墙）模板制安→设置标高控制点→"
        "二层梁板模板制安→线管预埋验收→验收→依次推进。"
    ),
    "3.9.3": (
        "钢筋工程施工工艺流程：熟悉图纸→钢筋下料→钢筋制作→钢筋绑扎（柱、墙、梁板）→验收。"
    ),
    "3.9.4": (
        "混凝土工程施工工艺流程：作业准备→混凝土搅拌→混凝土运输→"
        "柱、梁、板、剪力墙、楼梯混凝土浇筑与振捣→养护。"
    ),
    "3.9.5": (
        "砌体工程施工工艺流程：砌砖作业准备→砖浇水→砂浆搅拌→砌砖墙→验收。"
    ),
    "3.9.6": (
        "抹灰工程施工工艺流程：门窗框四周堵缝（或墙身预留线管、槽、孔洞）→墙面清理→"
        "粘贴加强网→墙体基层处理→吊垂直、套方、抹灰饼、冲筋→浇水湿润墙面→分层抹灰。"
    ),
    "3.9.7": (
        "楼地面水泥砂浆施工工艺流程：基层处理→找标高、弹线→洒水湿润→抹灰饼和标筋→"
        "搅拌砂浆→刷水泥浆结合层→铺水泥砂浆面层→木抹子搓平→铁抹子压第一遍→"
        "第二遍压光→第三遍压光→养护。"
    ),
    "3.9.8": (
        "饰件工程施工工艺流程：\n"
        "（1）室外饰件：基层处理→吊垂直、套方、找规矩→贴灰饼→抹底子灰→"
        "弹控制线，排砖，贴样板块→面砖粘贴→回缝→清理墙面；\n"
        "（2）室内饰件：清理基层→弹线→刷水泥素浆→水泥砂浆找平层→水泥浆结合层→"
        "铺贴饰件→回缝→清理墙面。"
    ),
    # ── 5.1.4 建设施工过程职业病危害因素识别 (定式 — 真稿10.1.4全量取证) ──
    # 零 LLM: 施工期危害识别为通用清单(火灾/起重伤害/高处坠落等), 不含项目特有数据
    "5.1.4": (
        "本项目施工、安装过程较复杂，多处存在高处作业、交叉重叠作业，起吊运输、焊接等"
        "一系列危险作业，极易发生事故。参照同类工程，在本项目的施工过程中存在的主要危险、"
        "有害因素有：火灾、起重伤害、高处坠落、触电、灼烫、物体打击、噪声危害、粉尘危害、"
        "车辆伤害等。\n"
        "施工单位作业过程中需根据各工种作业过程中接触的主要职业病危害因素采取一定的防护措施，"
        "做好工人职业健康监护，建筑施工过程中劳动者接触的主要职业病危害因素详见表5.1-2。"
    ),
}

_FIXED_PROJ = {"proj": "该项目"}


_LAWS_TEXT = None


def _load_laws_text() -> str:
    """法规清单全文 (通用法规列表, 跨项目相同; 数据源: fixed_texts_data.json 从原报告笔法提取)"""
    global _LAWS_TEXT
    if not _LAWS_TEXT:
        try:
            import json as _j
            from pathlib import Path as _P
            f = _P(__file__).parent / "fixed_texts_data.json"
            _LAWS_TEXT = _j.loads(f.read_text(encoding="utf-8")).get("laws_text", "")
        except Exception:
            _LAWS_TEXT = ""
    return _LAWS_TEXT


def _fixed_text(sec: str, project: dict | None = None) -> str | None:
    """若该节有固定文本(定式写法), 返回填充后的正文; 否则 None
    1.3.1 特例: 法规清单全文从 fixed_texts_data.json 动态加载 (通用法规列表, 跨项目相同)"""
    if sec == "1.3.1":
        laws = _load_laws_text()
        if laws:
            return ("本项目的评价依据主要包括以下法律、法规、规章（均以现行有效版本为准）：\n"
                    + laws
                    + "\n以上法规、规章及规范性文件的现行有效版本为本项目评价依据。")
    if sec == "1.3.2":
        try:
            import json as _j
            from pathlib import Path as _P
            std = _j.loads(_P(__file__).parent.joinpath("fixed_texts_data.json")
                           .read_text(encoding="utf-8")).get("std_text", "")
        except Exception:
            std = ""
        if std:
            return ("本项目的评价依据主要包括以下技术规范和标准（均以现行有效版本为准）：\n"
                    + std
                    + "\n评价中涉及的具体限值与检测方法，以上述标准现行有效版本为准。")
    tpl = FIXED_TEXTS.get(sec)
    if not tpl:
        return None
    name = ""
    if project:
        name = project.get("name", "")
        if name and name not in ("", "新建项目"):
            name = f"{name}"
    return tpl.format(**({**_FIXED_PROJ, "proj": name or _FIXED_PROJ["proj"]})) if "{proj}" in tpl else tpl


def _strip_md_tables(text: str) -> str:
    """剥离 LLM 误写入正文的 Markdown 表格 + 表标题行 (表格由系统生成插入)
    ① 行首为 '|' 的表格块 ② '表X.X-X ...' 标题行 (系统插表时会写真正的表标题)"""
    if "|" in text or re.search(r"^表\d", text, re.M):
        lines = text.split("\n")
        out, i = [], 0
        while i < len(lines):
            ln = lines[i].strip()
            is_tbl = ln.startswith("|") or (i + 1 < len(lines) and re.match(r"^\|?[\s:\-|]{5,}$", lines[i + 1].strip())
                                            and "|" in lines[i + 1])
            is_tbltitle = bool(re.match(r"^表\d{1,2}(\.\d+)*-\d+", ln)) and (len(ln) < 60 or "表格由系统插入" in ln)
            if is_tbl or is_tbltitle:
                while i < len(lines) and (lines[i].strip().startswith("|") or not lines[i].strip()
                                          or (bool(re.match(r"^表\d{1,2}(\.\d+)*-\d+", lines[i].strip()))
                                              and (len(lines[i].strip()) < 60 or "表格由系统插入" in lines[i].strip()))):
                    i += 1
                continue
            out.append(lines[i])
            i += 1
        res = "\n".join(out)
        res = re.sub(r"\n{3,}", "\n\n", res).strip()
        return res
    return text


def draft_sub(pid: str, sec: str, sub: str, _cache: dict | None = None) -> str:
    """二级小节 LLM 草稿 (可传预计算 _cache 提速)
    固定文本先行: 1.2评价目的/1.3.1法律依据 = GBZ/T 196 定式, 不走 LLM (防乱说话/编造)"""
    _fixed = _fixed_text(sub or sec)
    if _fixed:
        return _fixed
    c = _assess_cached(pid, _cache) if _cache is not None else _assess_cached(pid)
    from web.structure_data import get_chapter_info
    info = get_chapter_info(sub or sec, c["project"], c["assess"])
    from web.structure_data import chapter_must_cover
    info = info + "\n\n" + chapter_must_cover(sub or sec) if chapter_must_cover(sub or sec) else info
    prompt = build_sub_prompt(sub, info)
    if not prompt:
        return "暂不支持该小节(可扩展)"
    prompt = prompt.rstrip() + "\n" + _WRITING_GUIDE
    return _strip_md_tables(_llm(prompt))


def draft_section(pid: str, sec: str, _cache: dict | None = None) -> str:
    """生成某章 LLM 草稿 (报告1-9编号 → 引擎章节)"""
    c = _assess_cached(pid, _cache) if _cache is not None else _assess_cached(pid)
    project, assess = c["project"], c["assess"]
    from web.structure_data import get_chapter_info
    info = get_chapter_info(sec, project, assess)
    from web.structure_data import chapter_must_cover
    info = info + "\n\n" + chapter_must_cover(sec) if chapter_must_cover(sec) else info
    # 报告1-12章 → 标准10.2.x 章节prompt
    # ⚠ v46 章号平移后必须同步! 旧表只到 "11": "10.2.12", 导致:
    #   新 10 章(关键控制点) 被喂了 10.2.11(补充建议) 的 prompt → 串章
    #   新 12 章(结论) 无映射 → base_sec="12" → 无 prompt → 返回"暂不支持该章节"
    #   (实测: 完整生成 135/135 完成, 但第12章 0 字 —— 结论章整章空白)
    map_sec = {"1": "10.2.1", "2": "10.2.2", "3": "10.2.3", "4": "10.2.4",
               "5": "10.2.5", "6": "10.2.6", "7": "10.2.7", "8": "10.2.8",
               "9": "10.2.9", "10": "10.2.10", "11": "10.2.11", "12": "10.2.12"}
    base_sec = map_sec.get(sec, sec)
    if base_sec == "10.2.3":
        prompt = build_1023_prompt(project, assess, info)
    elif base_sec == "10.2.5":
        prompt = build_1025_prompt(assess, info)
    elif base_sec == "10.2.6":
        prompt = build_1026_prompt(info)
    else:
        prompt = build_section_prompt(base_sec, info)
    if not prompt:
        return "暂不支持该章节(可扩展)"
    # 附上质量导向写作标准 (替代裸字数限制: 靠信息密度而非字数控制篇幅)
    prompt = prompt.rstrip() + "\n" + _WRITING_GUIDE
    # 章节号统一: 用 report_struct 权威标题, 只把首行的旧编号去掉
    from web.report_struct import CHAPTERS
    sec_title = CHAPTERS.get(sec, "本项目")
    # 去掉 prompt 首行的旧编号痕迹, 替换正文里零散的 10.2.x
    import re
    prompt = re.sub(r"10\.2(\.\d+\.?\d*)?", f"第{sec}章", prompt)
    # 确保首行是标准标题
    first = prompt.split("\n")[0].strip()
    if not first.startswith("#"):
        prompt = f"# 第{sec}章 {sec_title}\n" + prompt
    return _strip_md_tables(_llm(prompt))


def draft_detail(pid: str, key: str, title: str, _cache: dict | None = None) -> str:
    """三级/四级单元 LLM 草稿 — 按标题 + 项目数据描述生成叙述段.

    key 形如 '2.1.1'/'5.8.2.1'/'8.4.1.1', title 为该项标题.
    固定文本先行: 1.3.1 等定式章节不走 LLM (与 draft_sub 同规则, 防乱说话).
    """
    _fixed = _fixed_text(key)
    if _fixed:
        return _fixed
    from web.report_struct import section_title, sub_title, sub3_title, sub4_title
    c = _assess_cached(pid, _cache) if _cache is not None else _assess_cached(pid)
    from web.structure_data import get_chapter_info
    info = get_chapter_info(key, c["project"], c["assess"])
    from web.structure_data import chapter_must_cover
    info = info + "\n\n" + chapter_must_cover(key) if chapter_must_cover(key) else info
    # 决定权威标题 (按 key 层级)
    parts = key.split(".")
    lv = len(parts)
    if lv == 3:
        canon = sub3_title(key)
    elif lv >= 4:
        canon = sub4_title(key)
    else:
        canon = title
    # 数据单元(产品/工段级, sub3_title/sub4_title查表返回原key): 用传入的 title
    # (修复: 5.1.1.2 这类数据四级传 key 给 LLM → LLM 自造"5.1.1.2 生产工艺过程中的职业病
    #  危害因素分析"标题; 应传真实标题如"饱和聚酯树脂")
    if canon == key and title and title != key:
        canon = title
    # 去掉标题里的编号前缀 (title 形如 "5.1.1.2 饱和聚酯树脂" → "饱和聚酯树脂")
    canon = re.sub(r"^\d+(\.\d+)*\s*", "", canon).strip() or canon
    # 构造 prompt: 用标题 + 项目数据 info, 生成该节叙述
    prompt = f"""撰写预评价报告「{canon}」小节的叙述段落。

{info}

要求:
1. 围绕「{canon}」展开, 结合本项目实际数据 (设备/工艺/危害/检测/防护)
2. 正式报告语言, 信息密度优先(见【写作标准】)
3. 无数据可引用的项, 用'依据标准推断'或'待补充', 不编造数值
4. 不引用【】/标签/表格格式
{_WRITING_GUIDE}"""
    return _strip_md_tables(_llm(prompt))


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "demo-cx"
    sec = sys.argv[2] if len(sys.argv) > 2 else "10.2.6"
    print(f"=== LLM 成文: {sec} (通用prompt) ===")
    print(draft_section(pid, sec))
