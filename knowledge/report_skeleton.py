"""报告模板骨架 — 全13节 (10.2.1~10.2.13) 章节结构+数据槽

设计:
  每节 = {"title": 标题, "paragraphs": [段落模板], "tables": [数据表模板]}
  段落模板用 {变量} 表示数据槽 (引擎输出填充)
  表格模板 = 列结构 + 数据来源 (引擎表名)
  数据槽来源: standard_db/judge/grade/identify/surveillance/ppe/illumination...

原则 (同 report_template):
  不学报告文字 (2篇过拟合风险), 骨架=GBZ/T 196 标准规定
  描述层 (危害特征/类比文字) 留 LLM+人工
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ===== 全13节骨架 =====
SECTION_SKELETON = {
    "10.2.1": {
        "title": "总论",
        "paragraphs": [
            "受{client}委托，按照《中华人民共和国职业病防治法》及国家职业卫生有关法律法规和标准，"
            "对{project}进行职业病危害预评价。",
            "评价依据：{basis_list}。",  # standard_db 26项引用
            "评价范围：{scope}。评价方法：{methods}。",
        ],
        "tables": [
            {"name": "评价依据表", "cols": ["序号", "标准号", "标准名称", "现行状态"],
             "source": "standard_db(REF_196)"},
            {"name": "项目概况表", "cols": ["项目名称", "项目性质", "建设地点", "生产规模"],
             "source": "project_input"},
        ],
    },
    "10.2.2": {
        "title": "现有企业概况",
        "paragraphs": [
            "现有企业建厂时间{est_date}，所属行业{industry}，企业规模{scale}，"
            "职工人数{workers}，生产工人数{prod_workers}，接触职业病危害因素人数{expo_workers}。",
            "现有企业职业卫生机构{mgt_org}，管理人员{mgt_staff}人，"
            "职业卫生管理制度{mgt_system}。",
        ],
        "tables": [
            {"name": "现有企业情况表", "cols": ["项目", "情况和数据", "备注"],
             "source": "existing_enterprise(15字段)"},
            {"name": "现有危害因素表", "cols": ["危害因素", "工种/岗位", "接触水平"],
             "source": "identify+judge"},
        ],
    },
    "10.2.3": {
        "title": "建设项目工程分析",
        "paragraphs": [
            "本项目主要工程内容包括{content}。",
            "选址：{site}。",
            "本项目产业链：{industry_chain}。",
        ],
        "tables": [
            {"name": "主要原辅材料表", "cols": ["序号", "名称", "CAS号", "年用量", "危害特性"],
             "source": "hazchem+process_extractor"},
            {"name": "主要设备表", "cols": ["序号", "设备名称", "规格", "内部物料"],
             "source": "equipment_material"},
            {"name": "照明照度表", "cols": ["房间/场所", "参考平面", "照度标准值", "UGR", "Ra"],
             "source": "illumination_std"},
        ],
    },
    "10.2.3.7": {
        "title": "建筑卫生学分析与评价",
        "paragraphs": [
            "本项目建筑卫生学对照GBZ 1、GB 50019、GB 50033、GB/T 50034等标准分析。",
            "供暖通风：{hvac}。",
        ],
        "tables": [
            {"name": "建筑卫生学检查表", "cols": ["序号", "卫生要求", "检查依据", "检查结果"],
             "source": "gbz1_rule"},
        ],
    },
    "10.2.3.8": {
        "title": "辅助用室分析与评价",
        "paragraphs": ["车间卫生特征等级：{grade}。辅助用室配置情况：{aux_rooms}。"],
        "tables": [
            {"name": "辅助用室配置表", "cols": ["车间卫生特征", "浴室", "更衣室", "盥洗", "依据"],
             "source": "gbz1_rule(7.2.x)"},
        ],
    },
    "10.2.4": {
        "title": "类比企业（项目）调查与分析",
        "paragraphs": [
            "类比项目可比性分析：{comparability_summary}。",
            "类比项目职业病防护及职业卫生管理水平：{analogy_level}。",
        ],
        "tables": [
            {"name": "类比项目可比性表", "cols": ["比较条件", "拟建项目", "类比项目", "比较结果"],
             "source": "analogy_engine(9要素)"},
        ],
    },
    "10.2.5": {
        "title": "职业病危害因素及危害程度分析",
        "paragraphs": [
            "本项目可能产生的主要职业病危害因素为{hazards}。",
            "有毒物料、设备密闭不严处跑冒滴漏、{gen_sources}等是主要产生环节。",
            "对照GBZ 2.1—2019和GBZ 2.2—2007标准，本项目各岗位职业病危害因素预期接触水平判定结果如下：",
        ],
        "tables": [
            {"name": "危害因素识别表", "cols": ["序号", "职业病危害因素", "产生环节", "接触岗位", "检测方法"],
             "source": "identify_hazards"},
            {"name": "判定表", "cols": ["序号", "职业病危害因素", "CTWA", "PC-TWA", "CSTEL", "PC-STEL", "判定"],
             "source": "judge_chemical"},
            {"name": "作业分级表", "cols": ["序号", "职业病危害因素", "G值", "作业级别"],
             "source": "grade_engine"},
        ],
    },
    "10.2.6": {
        "title": "职业病防护设施分析与评价",
        "paragraphs": ["本项目拟采取的防护设施：{protection_measures}。"],
        "tables": [
            {"name": "防护设施检查表", "cols": ["序号", "防护设施类别", "拟采取措施", "依据"],
             "source": "protection_rule"},
        ],
    },
    "10.2.7": {
        "title": "应急救援措施的分析与评价",
        "paragraphs": ["本项目应急救援措施：{emergency_measures}。"],
        "tables": [
            {"name": "应急救援检查表", "cols": ["序号", "场景", "检查项", "要求", "依据"],
             "source": "emergency_rule"},
        ],
    },
    "10.2.8": {
        "title": "个人使用的职业病防护用品的分析与评价",
        "paragraphs": ["本项目各岗位拟配置的个人防护用品：{ppe_list}。"],
        "tables": [
            {"name": "PPE配备表", "cols": ["序号", "岗位/危害", "防护装备", "产品标准"],
             "source": "ppe_item+ppe_for_hazards"},
        ],
    },
    "10.2.9": {
        "title": "职业卫生管理的分析与评价",
        "paragraphs": ["本项目职业卫生管理措施：{management_measures}。"],
        "tables": [
            {"name": "健康监护表", "cols": ["序号", "危害因素", "检查类别", "周期", "必检项目"],
             "source": "surveillance_rule"},
            {"name": "管理制度检查表", "cols": ["序号", "制度类别", "检查点", "依据"],
             "source": "management_rule"},
        ],
    },
    "10.2.10": {
        "title": "职业病危害关键控制点分析",
        "paragraphs": ["本项目职业病危害关键控制点：{control_points}。"],
        "tables": [
            {"name": "关键控制点表", "cols": ["序号", "工序", "危害因素", "综合评分", "控制级别"],
             "source": "control_point_engine"},
        ],
    },
    "10.2.11": {
        "title": "职业病防治措施的补充建议",
        "paragraphs": ["针对{issues}，提出以下补充建议：{suggestions}。"],
        "tables": [
            {"name": "问题与建议表", "cols": ["序号", "存在问题", "建议措施", "依据"],
             "source": "conclusion_engine(problems)"},
        ],
    },
    "10.2.12": {
        "title": "结论与建议",
        "paragraphs": [
            "该项目属于职业病危害{category}的建设项目。存在问题{problems}项；"
            "在采取预评价报告提出的职业病防治措施补充建议后，职业病防治方面{feasibility}。",
        ],
        "tables": [
            {"name": "结论要素表", "cols": ["序号", "结论要素", "结论"],
             "source": "conclusion_engine"},
        ],
    },
    "10.2.13": {
        "title": "预评价报告格式",
        "paragraphs": ["本报告按照GBZ/T 196—2025附录D格式编写（A4、仿宋、小四、28行×30字）。"],
        "tables": [],
    },
}


def skeleton_summary() -> dict:
    """骨架概览 (13节 段落数/表格数)"""
    out = {}
    for sec, sk in SECTION_SKELETON.items():
        out[sec] = {"title": sk["title"], "paragraphs": len(sk["paragraphs"]),
                    "tables": len(sk["tables"])}
    return out


if __name__ == "__main__":
    print("=== 报告模板骨架 (全13节) 概览 ===\n")
    total_p = total_t = 0
    for sec, info in skeleton_summary().items():
        total_p += info["paragraphs"]
        total_t += info["tables"]
        mark = "🔵" if info["tables"] > 0 else "🟡"
        print(f"  {sec:10s} {info['title']:22s} 段落{info['paragraphs']} 表格{info['tables']} {mark}")
    print(f"\n总计: 段落 {total_p}, 表格 {total_t} (13节)")
    print("\n数据槽来源: standard_db/existing_enterprise/industry/identify/judge/"
          "grade/analogy/protection/emergency/ppe/surveillance/management/"
          "control_point/conclusion (14引擎表)")
