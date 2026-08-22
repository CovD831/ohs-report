"""报告骨架 — 按报告真实结构 (长兴/浦发两篇一致)

正文 (1-6章, 两篇完全一致):
  1 建设项目概况         1.1 基本情况 1.2 项目组成 1.3 施工概况
  2 职业病危害因素识别与评价 2.1 识别 2.2 风险评价
  3 职业病危害防护措施评价  3.1 防护设施 3.2 PPE 3.3 应急
  4 综合性评价           4.1 选址布局 4.2 工艺设备 4.3 建筑卫生学
                          4.4 辅助用室 4.5 管理 4.6 投资
  5 职业病补充措施及建议   5.1 三同时 5.2 补充措施 5.3 培训告知
                          5.4 警示标识 5.5 监护 5.6 受限空间 5.7 应急 5.8 施工监理
  6 评价结论
附录:
  7 评价要点            7.1 项目背景 ... 7.8 质量控制
  8 工程分析            8.1 工程概况 ... 8.7 施工分析
  9 类比调查分析         9.1 类比选择 ... 9.6 综合结论

引擎映射: 每节 source=现有引擎/规则表 (数据来源不变)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 报告骨架 (编号/标题/引擎来源)
SECTION_SKELETON = {
    "1": {
        "title": "建设项目概况",
        "paragraphs": ["受{client}委托，对{project}进行职业病危害预评价。本报告评价范围为该项目投产运行期间。"],
        "tables": [{"name": "项目基本情况表", "cols": ["项目名称", "项目性质", "建设地点", "生产规模"],
                    "source": "project_input"},
                   {"name": "项目组成表", "cols": ["工程内容", "建设内容", "备注"], "source": "project_input"}],
        "engine": "工程分析",
    },
    "2": {
        "title": "职业病危害因素识别与评价",
        "paragraphs": ["本项目可能产生的主要职业病危害因素为{hazards}。"],
        "tables": [{"name": "危害因素识别表", "cols": ["序号", "危害因素", "产生环节", "接触岗位", "检测方法"],
                    "source": "identify"},
                   {"name": "判定表", "cols": ["序号", "危害因素", "CTWA", "PC-TWA", "CSTEL", "PC-STEL", "判定"],
                    "source": "judge"},
                   {"name": "作业分级表", "cols": ["序号", "危害因素", "G值", "作业级别"], "source": "grade"}],
        "engine": "危害分析",
    },
    "3": {
        "title": "职业病危害防护措施评价",
        "paragraphs": ["本项目拟采取的防护措施：{protection}。"],
        "tables": [{"name": "防护设施检查表", "cols": ["序号", "危害类别", "检查点", "依据"],
                    "source": "protection_rule"},
                   {"name": "PPE配备表", "cols": ["序号", "岗位/危害", "防护装备", "标准"], "source": "ppe"},
                   {"name": "应急救援检查表", "cols": ["序号", "场景", "要求", "依据"], "source": "emergency_rule"}],
        "engine": "防护应急",
    },
    "4": {
        "title": "综合性评价",
        "paragraphs": ["本项目选址、总体布局、建筑卫生学、辅助用室、职业卫生管理、专项投资评价如下。"],
        "tables": [{"name": "建筑卫生学检查表", "cols": ["序号", "卫生要求", "检查依据", "检查结果"],
                    "source": "gbz1_rule"},
                   {"name": "照度标准表", "cols": ["序号", "房间/场所", "参考面", "照度lx"], "source": "illumination"},
                   {"name": "管理制度检查表", "cols": ["序号", "制度类别", "检查点", "依据"], "source": "management_rule"}],
        "engine": "综合",
    },
    "5": {
        "title": "职业病补充措施及建议",
        "paragraphs": ["针对本项目存在问题，提出以下补充建议：{advice}。"],
        "tables": [{"name": "问题与建议表", "cols": ["序号", "存在问题", "建议措施(标准条款)", "依据"],
                    "source": "advice"}],
        "engine": "建议",
    },
    "6": {
        "title": "评价结论",
        "paragraphs": ["该项目属于职业病危害{category}的建设项目。存在问题{problems}项；在采取补充建议后，职业病防治方面{feasibility}。"],
        "tables": [{"name": "结论要素表", "cols": ["序号", "结论要素", "结论"], "source": "conclusion"}],
        "engine": "结论",
    },
    "7": {
        "title": "资料性附录 评价要点",
        "paragraphs": ["评价依据：{basis}。评价范围：{scope}。评价方法：{methods}。"],
        "tables": [{"name": "评价依据表", "cols": ["序号", "标准号", "标准名称", "现行状态"], "source": "standard_db"}],
        "engine": "总论",
    },
    "8": {
        "title": "资料性附录 工程分析",
        "paragraphs": ["本项目行业分类：{industry}，主要设备、工艺、物料见下表。"],
        "tables": [{"name": "设备清单", "cols": ["序号", "设备名称", "内部物料"], "source": "equipment"},
                   {"name": "原辅材料表", "cols": ["序号", "名称", "来源", "危害类别"], "source": "process"},
                   {"name": "行业链", "cols": ["层级", "名称"], "source": "industry"}],
        "engine": "工程分析详",
    },
    "9": {
        "title": "资料性附录 类比调查分析",
        "paragraphs": ["类比项目可比性：{comparability}。"],
        "tables": [{"name": "类比可比性表", "cols": ["序号", "比较要素", "拟建项目", "类比项目"], "source": "analogy"}],
        "engine": "类比",
    },
}


def section_ids() -> list[str]:
    return list(SECTION_SKELETON.keys())


def titles() -> dict[str, str]:
    return {k: v["title"] for k, v in SECTION_SKELETON.items()}


if __name__ == "__main__":
    print("=== 报告骨架 (按报告真实结构) ===\n")
    for sec, sk in SECTION_SKELETON.items():
        print(f"  {sec:4s} {sk['title']:24s} 段落{len(sk['paragraphs'])} 表格{len(sk['tables'])} "
              f"[引擎: {sk['engine']}]")
    print(f"\n共 {len(SECTION_SKELETON)} 节 (1-6正文 + 7-9附录)")
