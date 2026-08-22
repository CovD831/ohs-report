"""作业分级引擎 — GBZ/T 229.1-3—2025 (10.2.5.3 危害程度分级)

三部分 (2025-08-20发布, 2026-02-01实施, 代替2010版):
  229.1 生产性粉尘 — 五级 (0/Ⅰ行动水平/Ⅱ轻度/Ⅲ中度/Ⅳ高度), G=WM×WB×WL
  229.2 化学物     — 四级 (0/Ⅰ/Ⅱ/Ⅲ), G=WD×WB×WL, B>1强制≥Ⅱ级
  229.3 高温作业   — 表1查表法 (体力强度×接触时间率×WBGT效应值)

权重表:
  WM 游离SiO2: <10%=1 | 10-50%=2 | 50-80%=3 | >80%=4
  WD 危害程度(GBZ/T 230): 轻度1/中度2/高度4/极度8 (高毒/剧毒=8)
  WB 接触比值: B≤0.5→0 | B>0.5→B
  WL 体力强度: Ⅰ=1.0 | Ⅱ=1.5 | Ⅲ=2.0 | Ⅳ=2.5

用法: python3 -m knowledge.grade_engine
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---- 权重表 ----
WM_TABLE = {"<10": 1, "10-50": 2, "50-80": 3, ">80": 4}   # 游离SiO2含量(%)
WD_TABLE = {"轻度危害": 1, "中度危害": 2, "高度危害": 4, "极度危害": 8}
WL_TABLE = {"Ⅰ": 1.0, "Ⅱ": 1.5, "Ⅲ": 2.0, "Ⅳ": 2.5}

# 229.3 高温分级表1 (从标准原文 find_tables 精确提取, 2025版)
# 体力强度 × 接触时间率% × WBGT效应值列(28/29/30/31/32/33/34/35/36)
# 值含义: — = 未达Ⅰ级 | Ⅰ/Ⅱ/Ⅲ/Ⅳ = 分级 | * = 超过Ⅳ级需专门热应激评估
HEAT_TABLE = {
    "Ⅰ": {
        "0-25": ["—", "—", "—", "—", "—", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ"],
        "25-50": ["—", "—", "—", "—", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "*"],
        "50-75": ["—", "—", "—", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "*", "*"],
        "75-100": ["—", "—", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "*", "*", "*"],
    },
    "Ⅱ": {
        "0-25": ["—", "—", "—", "—", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "*"],
        "25-50": ["—", "—", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "*", "*", "*"],
        "50-75": ["—", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "*", "*", "*", "*"],
        "75-100": ["Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "*", "*", "*", "*", "*"],
    },
    "Ⅲ": {
        "0-25": ["—", "—", "—", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "*", "*"],
        "25-50": ["—", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "*", "*", "*", "*"],
        "50-75": ["Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "*", "*", "*", "*", "*"],
        "75-100": ["*", "*", "*", "*", "*", "*", "*", "*", "*"],
    },
    "Ⅳ": {
        "0-25": ["—", "—", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "*", "*", "*"],
        "25-50": ["Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "*", "*", "*", "*", "*"],
        "50-75": ["*", "*", "*", "*", "*", "*", "*", "*", "*"],
        "75-100": ["*", "*", "*", "*", "*", "*", "*", "*", "*"],
    },
}
HEAT_BANDS = [28, 29, 30, 31, 32, 33, 34, 35, 36]  # WBGT效应值对应的列


# ---- 229.2 化学物分级 ----
def grade_chemical(wd_level: str, b: float, labor: str) -> dict:
    """化学物作业分级: G = WD×WB×WL (229.2 表5)
    wd_level: 轻度危害/中度危害/高度危害/极度危害 (GBZ/T 230)
    b: 接触比值 C/PC-TWA (或CSTE/PC-STEL, CME/MAC)
    labor: 体力劳动强度 Ⅰ/Ⅱ/Ⅲ/Ⅳ"""
    wd = WD_TABLE.get(wd_level, 4)     # 缺省按高度危害(保守)
    wb = 0 if b <= 0.5 else b
    wl = WL_TABLE.get(labor, 1.5)
    g = wd * wb * wl
    if g == 0:
        level, name = "0", "相对无害作业"
    elif g <= 6:
        level, name = "Ⅰ", "轻度危害作业"
    elif g <= 24:
        level, name = "Ⅱ", "中度危害作业"
    else:
        level, name = "Ⅲ", "重度危害作业"
    # 注: B>1 则为Ⅱ级或以上
    if b > 1 and level == "Ⅰ":
        level, name = "Ⅱ", "中度危害作业"
    return {"g": round(g, 2), "wd": wd, "wb": wb, "wl": wl,
            "level": level, "name": name,
            "note": "G=WD×WB×WL (229.2 式4/表5)" if not (b > 1 and level == "Ⅱ")
                    else "B>1强制≥Ⅱ级 (表5注)"}


# ---- 229.1 粉尘分级 ----
def grade_dust(sio2_band: str, b: float, labor: str) -> dict:
    """粉尘作业分级: G = WM×WB×WL (229.1 式1/表4) 五级
    sio2_band: 游离SiO2含量 <10/10-50/50-80/>80 (%)
    b: 接触比值 C/PC-TWA
    labor: 体力劳动强度"""
    wm = WM_TABLE.get(sio2_band, 4)
    wb = 0 if b <= 0.5 else b
    wl = WL_TABLE.get(labor, 1.5)
    g = wm * wb * wl
    if g == 0 and b <= 0.5:
        level, name = "0", "相对无害作业"
    elif g == 0 and b > 0.5:
        level, name = "Ⅰ", "行动水平作业"
    elif g <= 6:
        level, name = "Ⅱ", "轻度危害作业"
    elif g <= 16:
        level, name = "Ⅲ", "中度危害作业"
    else:
        level, name = "Ⅳ", "高度危害作业"
    return {"g": round(g, 2), "wm": wm, "wb": wb, "wl": wl,
            "level": level, "name": name, "note": "G=WM×WB×WL (229.1 式1/表4)"}


# ---- 229.3 高温分级 ----
_LABOR_MAP = {"I": "Ⅰ", "II": "Ⅱ", "III": "Ⅲ", "IV": "Ⅳ",
              "Ⅰ": "Ⅰ", "Ⅱ": "Ⅱ", "Ⅲ": "Ⅲ", "Ⅳ": "Ⅳ",
              "ⅰ": "Ⅰ", "ⅱ": "Ⅱ", "ⅲ": "Ⅲ", "ⅳ": "Ⅳ"}


def _norm_labor(labor: str) -> str:
    """体力劳动强度归一: 拉丁 I/II 与罗马 Ⅰ/Ⅱ 统一 (官方PDF文本层为拉丁I)"""
    return _LABOR_MAP.get(str(labor).strip().upper().replace("Ⅳ", "IV").replace("Ⅲ", "III").replace("Ⅱ", "II").replace("Ⅰ", "I"), "Ⅱ")


def grade_heat(labor: str, rate_pct: float, wbgt_eff: float) -> dict:
    """高温作业分级: 表1查表法
    labor: 体力劳动强度 Ⅰ/Ⅱ/Ⅲ/Ⅳ (兼容拉丁 I/II/III/IV)
    rate_pct: 高温作业接触时间率 (%)
    wbgt_eff: WBGT指数效应值 (℃, 已含服装调整值CAV)"""
    labor_n = _norm_labor(labor)
    # 接触时间率分段
    if rate_pct <= 25:
        rate_band = "0-25"
    elif rate_pct <= 50:
        rate_band = "25-50"
    elif rate_pct <= 75:
        rate_band = "50-75"
    else:
        rate_band = "75-100"
    # WBGT 列定位: 找最大的 band ≤ wbgt_eff (向下取整: 30.5℃ → 30列)
    col = 0
    for i, b in enumerate(HEAT_BANDS):
        if wbgt_eff >= b - 0.001:
            col = i
    val = HEAT_TABLE.get(labor_n).get(rate_band, ["—"] * 9)[col]
    names = {"0": "0级(未达分级)", "Ⅰ": "Ⅰ级(轻度危害)", "Ⅱ": "Ⅱ级(中度危害)",
             "Ⅲ": "Ⅲ级(重度危害)", "Ⅳ": "Ⅳ级(极重度)"}
    if val == "—":
        return {"level": "0", "name": "未达Ⅰ级分级范围",
                "wbgt_band": HEAT_BANDS[col], "note": "229.3 表1"}
    return {"level": val, "name": names.get(val, val),
            "wbgt_band": HEAT_BANDS[col],
            "note": "229.3 表1 (体力×时间率×WBGT)" if val != "*"
                    else "229.3 表1 *号: 超Ⅳ级,需专门热应激评估"}


if __name__ == "__main__":
    print("=== 作业分级引擎演示 (GBZ/T 229.1-3—2025) ===")
    print("\n[229.2 化学物] 苯 (高度危害4, B=0.7, Ⅱ级劳动):")
    print(" ", grade_chemical("高度危害", 0.7, "Ⅱ"))
    print("\n[229.2 化学物] 高毒物剧毒(B=1.2, Ⅰ级劳动) — WD强制8:")
    print(" ", grade_chemical("极度危害", 1.2, "Ⅰ"))
    print("\n[229.1 粉尘] 矽尘(SiO2>80% WM=4, B=0.8, Ⅲ级劳动):")
    print(" ", grade_dust(">80", 0.8, "Ⅲ"))
    print("\n[229.3 高温] Ⅱ级劳动, 接触时间率60%, WBGT 32℃:")
    print(" ", grade_heat("Ⅱ", 60, 32))
    print("\n[229.3 高温] Ⅲ级劳动, 100%, WBGT 35℃:")
    print(" ", grade_heat("Ⅲ", 100, 35))
