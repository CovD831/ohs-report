"""单测: PPE 表必须是「竖排 6 列」— 防横排 √/※ 矩阵复活

背景(版式约定复刻):
  真稿 PPE 表(表4.2-4 / 8.1-1) = 竖排 6 列
    生产单元 | 生产岗位 | 配置的防护用品 | 单位 | 数量 | 更换周期
  真稿 43 张表**零张** √/※ 矩阵表 → 我方旧横排矩阵是张冠李戴。

  历史上出过两次:
    1. section_filler 现算走了 _ppe_matrix_table (已弃用)
    2. project.data.built_tables 沉淀了旧矩阵壳, 导出「骨架优先」覆盖现算
       (tools/migrate_ppe_matrix.py 迁移)
  本测试锁死结构 + 单位推断 + 周期归一化, 防再次回归。

运行: python tools/test_ppe_vertical.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from knowledge.ppe_vertical import build_ppe_rows, unit_of, _norm_period  # noqa: E402

# migrate_ppe_matrix 在 tools/ 下 (非同包), 直接按路径加载
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "migrate_ppe_matrix", str(ROOT / "tools" / "migrate_ppe_matrix.py"))
_mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mm)
is_ppe_matrix = _mm.is_ppe_matrix

ok = fail = 0


def check(desc, got, want):
    global ok, fail
    good = got == want
    ok += good
    fail += (not good)
    print(f"  {'✓' if good else '✗'} {desc}")
    if not good:
        print(f"      期望={want!r}  实得={got!r}")


print("### 1. 竖排表结构 (真稿 6 列)")
COLS = ["生产单元", "生产岗位", "配置的防护用品", "单位", "数量", "更换周期"]
pd_data = {"ppe": [
    {"item": "安全帽", "post": "合成", "frequency": "每 2.5年", "unit_name": "六氟磷酸锂", "count": "1"},
    {"item": "安全鞋", "post": "合成", "frequency": "2年半", "unit_name": "六氟磷酸锂", "count": "1"},
    {"item": "防噪耳塞", "post": "晶析", "frequency": "半年", "unit_name": "六氟磷酸锂", "count": "1"},
]}
rows, merges = build_ppe_rows(pd_data)
check("有行产出", len(rows) > 0, True)
check("★每行 6 列", all(len(r) == 6 for r in rows), True)
check("列数=6", len(rows[0]) if rows else 0, 6)
check("单位列=只/双/副", [r[3] for r in rows], ["只", "双", "副"]) if len(rows) == 3 else None
check("★周期已归一(每2.5年)", rows[0][5] if rows else "", "每2.5年")
check("★2年半→每2.5年", rows[1][5] if len(rows) > 1 else "", "每2.5年")
check("★半年→每半年", rows[2][5] if len(rows) > 2 else "", "每半年")
check("★返回类型 (rows, set)", isinstance(merges, set), True)
# merges = 「前两列(单元+岗位)均与上一行相同 → vMerge continue」的行索引
# 测试数据: R0(六氟磷酸锂/合成) R1(六氟磷酸锂/合成) R2(六氟磷酸锂/晶析)
#   → R1 与 R0 同单元同岗位 → 标记; R2 岗位不同 → 不标记
check("★同单元同岗位续行 (仅索引1)", sorted(merges), [1])

print("\n### 2. 单位推断 (用品名属性, 非经验值)")
for item, want in [("安全帽", "只"), ("安全鞋", "双"), ("防护眼镜", "副"),
                   ("胶手套", "副"), ("化学防护服", "套"), ("耐酸碱胶靴", "双"),
                   ("防噪耳塞", "副"), ("安全带", "条"), ("未知品", "—")]:
    check(f"unit_of({item!r})", unit_of(item), want)

print("\n### 3. 周期归一化")
for src, want in [("每 2.5年", "每2.5年"), ("2年半", "每2.5年"), ("1年半", "每1.5年"),
                  ("半年", "每半年"), ("6个月", "每6个月"), ("每年", "每年"),
                  ("按需领用", "按需领用"), ("以旧换新", "以旧换新"), ("", "待补充")]:
    check(f"_norm_period({src!r})", _norm_period(src), want)

print("\n### 4. 矩阵识别 (迁移工具判定)")
MATRIX = {"cols": ["安全装备项目／工作性质、内容", "安全鞋", "绝缘鞋"],
          "rows": [["取样作业", "√", ""], ["投料作业", "√", "√"]]}
VERT = {"cols": COLS, "rows": [["六氟磷酸锂", "合成", "安全帽", "只", "1", "每2.5年"]]}
check("★旧矩阵被识别", is_ppe_matrix(MATRIX), True)
check("★竖排表不误判", is_ppe_matrix(VERT), False)
check("空表不误判", is_ppe_matrix({"cols": [], "rows": []}), False)
BIG = {"cols": ["作业"] + [f"E{i}" for i in range(12)],
       "rows": [["取样"] + ["√"] * 12, ["投料"] + ["√"] * 12]}
check("★≥10列纯√/※ 也识别", is_ppe_matrix(BIG), True)

print()
print(f"通过 {ok} / {ok + fail}")
if fail:
    print(f"✗ {fail} 项失败")
sys.exit(1 if fail else 0)
