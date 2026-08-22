"""二级小节内容生成 — 按 1.x/2.x/3.x 小节生成 (数据驱动)

设计: 二级小节 = 一级内容的精分
  每个小节映射到引擎数据 (表格子集) + 专用段落 + LLM可补
  三级以下(项目特有)不预设, 数据驱动
映射: 小节 → (段落模板, 表格源)
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from web.section_filler import fill_section  # noqa: E402


def paragraph_for_sub(conn: sqlite3.Connection, sub: str, assess: dict) -> list[str]:
    """按小节编号生成段落"""
    hazards = assess.get("hazards", [])
    names = "、".join(h["factor"] for h in hazards[:8]) if hazards else "—"
    chain = assess.get("industry_chain") or {}
    risk = assess.get("industry_risk") or {}

    subs = {
        "1.1": ["本项目为{project}，属于{mid}行业，项目性质/规模/地点详见下表。"],
        "1.2": ["项目主要由生产装置、辅助及公用工程组成，主要设备见下表。"],
        "1.3": ["建设施工期：基坑开挖、设备安装、涂装等，存在粉尘、噪声、电焊烟尘等危害。"],
        "2.1": ["生产工艺过程可能产生的职业病危害因素：{names}。"],
        "2.2": ["本项目各岗位接触水平判定结果如下（GBZ 2.1—2019/GBZ 2.2—2007）。"],
        "3.1": ["本项目拟采取防毒、防尘、防噪声、防高温等防护设施，详见检查表。"],
        "3.2": ["各岗位拟配置个人防护用品：化学→防毒面具/化学防护服，粉尘→KN95，噪声→耳塞。"],
        "3.3": ["本项目应急救援包括应急喷淋洗眼设备、毒气报警、应急预案及演练。"],
        "4.1": ["本项目选址于化工园区，总体布局遵循功能分区，产生危害车间布置合理。"],
        "4.2": ["生产工艺采用密闭化管道化，设备布局符合GBZ 1要求。"],
        "4.3": ["建筑卫生学：车间卫生特征等级、采光照度（GB/T 50034—2024）、暖通，详见下表。"],
        "4.4": ["辅助用室按车间卫生特征等级配置（浴室/更衣室/盥洗，GBZ 1 表10/11）。"],
        "4.5": ["职业卫生管理：机构/制度/监护/检测/培训，详见检查表（GBZ/T 225—2010）。"],
        "4.6": ["职业道德卫生专项投资含防护设施/检测/应急预案/PPE/体检等。"],
        "5.1": ["建设项目职业病防护设施与主体工程同时设计、同时施工、同时投入生产使用。"],
        "5.2": ["针对本项目存在的职业病危害，提出以下补充措施建议。"],
        "5.3": ["对接触职业病危害因素的劳动者开展岗前、在岗职业卫生培训。"],
        "5.4": ["产生职业病危害的岗位设置警示标识和中文警示说明（GBZ 158—2003）。"],
        "5.5": ["按GBZ 188—2025对接触危害因素劳动者开展上岗前、在岗、离岗健康检查。"],
        "5.6": ["存在密闭空间（储罐/反应釜等）的作业应执行GBZ/T 205—2007（氧18-22%、可燃<10%LEL）。"],
        "5.7": ["应急救援预案、应急设施配置及演练安排。"],
        "5.8": ["施工单位及监理单位职业病防治措施：粉尘防护、个体防护、健康监护、专项投资。"],
        "7.1": ["本项目背景：{project}。"],
        "7.2": ["评价目的：识别职业病危害，提出防治措施，为职业病危害分类管理提供依据。"],
        "7.3": ["评价依据：职业病防治法及GBZ标准体系（详见评价依据表）。"],
        "7.4": ["评价范围：项目投产运行期间主要职业病危害因素及防治情况。"],
        "7.5": ["评价内容：工程分析、危害识别、防护措施、管理、结论。"],
        "7.6": ["评价方法：工程分析法、类比法、检查表法、风险评估法。"],
        "7.7": ["评价程序：准备→工程分析→类比→检测→评价→报告。"],
        "7.8": ["质量控制：资料审核、检测资质、三级审核。"],
        "8.1": ["工程概况：{project}，{mid}行业。"],
        "8.2": ["总平面布置：生产区/辅助区/公用区分区布置。"],
        "8.3": ["建筑卫生学参数：采光照度/通风换气。"],
        "8.4": ["工艺流程：{proc}，设备布局密闭化。"],
        "8.5": ["主要原辅材料：{names}。"],
        "8.6": ["辅助用室：浴室/更衣室/盥洗/休息室配置。"],
        "8.7": ["建设施工工程分析：施工阶段职业病危害因素。"],
        "9.1": ["类比企业选择：同行业/同原料/同工艺优先。"],
        "9.2": ["类比企业职业卫生调查：工程/防护/检测情况。"],
        "9.3": ["类比企业职业卫生管理：制度/监护情况。"],
        "9.4": ["类比企业职业危害检测结果：各因素浓度/强度及限值。"],
        "9.5": ["类比企业职业健康监护：检查项目/周期。"],
        "9.6": ["类比调查综合结论：可比性判断。"],
    }
    tpl = subs.get(sub, [])
    return [p.format(project=assess.get("project", ""), mid=(chain.get("mid") or {}).get("name", "—"),
                     names=names, proc="密闭管道化自动化") for p in tpl]


def tables_for_sub(conn: sqlite3.Connection, sub: str, assess: dict) -> list[dict]:
    """小节 → 表格 (从一级表中按小节裁剪)"""
    all_t = fill_section(conn, sub.split(".")[0], assess)
    if not all_t:
        return []
    name_map = {
        "1.1": ["主要设备清单", "劳动定员表"],
        "1.2": ["主要设备清单"],
        "2.1": ["危害因素识别表"],
        "2.2": ["检测结果表", "判定表"],
        "3.1": ["防护设施检查表"],
        "3.2": ["PPE配备表"],
        "3.3": ["应急救援检查表"],
        "4.3": ["照度标准表"],
        "4.5": ["管理制度检查表", "职业健康监护表"],
        "4.4": ["职业健康监护表"],
        "7.3": ["评价依据表"],
        "8.5": ["原辅材料表"],
        "8.4": ["行业链"],
    }
    want = name_map.get(sub, [])
    return [t for t in all_t if t["name"] in want] or all_t[:0]


def build_subsection(conn: sqlite3.Connection, sec: str, sub: str, assess: dict) -> dict:
    """小节完整内容 (段落+表格)"""
    paras = paragraph_for_sub(conn, sub, assess)
    tables = tables_for_sub(conn, sub, assess)
    return {"paragraphs": paras, "tables": tables}


if __name__ == "__main__":
    from knowledge.project_assess import assess_project
    from knowledge.oel import connect
    conn = connect()
    r = assess_project(conn, {"name": "t", "industry": "261", "equipment": ["酯化釜"],
                              "detections": [{"factor": "甲苯", "ctwa": 0.5}]})
    for sub in ("2.1", "2.2", "3.1", "4.5", "7.3"):
        out = build_subsection(conn, sub.split(".")[0], sub, r)
        print(f"{sub}: 段落{len(out['paragraphs'])} 表格{[(t['name'], len(t['rows'])) for t in out['tables']]}")
    conn.close()
