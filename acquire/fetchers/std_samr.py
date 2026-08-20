"""全国标准信息公共服务平台 — 标准目录/状态抓取器
已验证: JSON API 直连可用, 返回标准现行/废止状态 (引用时效核验权威源)
注意: 该平台只覆盖 GB/GB/T/行业/地方/团体/企业标准, 不含 GBZ 系列 (见 nhc_gbz.py)
输出: acquire/raw/std_samr/standards_status.json
"""
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # 项目根
from acquire.base import PoliteFetcher  # noqa: E402

BASE = "https://std.samr.gov.cn/gb/search/gbQueryPage"
RAW_DIR = Path(__file__).resolve().parent.parent / "raw" / "std_samr"

# 注: 仅 GB/GB/T 体系 (GBZ/T 196-2025 第10章引用的国标 + 行业分类)
NEEDED = [
    "GB/T 4754-2017",    # 国民经济行业分类 (风险分类前置)
    "GB 5083",           # 生产设备安全卫生设计总则
    "GB/T 12801",        # 生产过程安全卫生要求总则
    "GB 39800",          # 个体防护装备配备规范
    "GB/T 18664",        # 呼吸防护用品的选择、使用与维护
    "GB/T 23466",        # 护听器的选择指南
    "GB/T 38144",        # 眼面部防护
    "GB 50187",          # 工业企业总平面设计规范
    "GB 50019",          # 工业建筑供暖通风与空气调节设计规范
    "GB 50033",          # 建筑采光设计标准
    "GB/T 50034",        # 建筑照明设计标准
    "GB 50073",          # 洁净厂房设计规范
    "GB/T 16758",        # 排风罩的分类及技术条件
    "GB/T 50087",        # 工业企业噪声控制设计规范
]


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def fetch(standards: list[str] | None = None) -> dict:
    """按标准号查询, 落盘 JSON (温和抓取: 指数退避+限速由 PoliteFetcher 保证)"""
    fetcher = PoliteFetcher("std_samr")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for code in (standards or NEEDED):
        try:
            url = f"{BASE}?searchText={urllib.parse.quote(code)}&type=all"
            data = fetcher.get_json(url)
            rows = []
            for r in data.get("rows", []):
                rows.append({
                    "code": strip_tags(r.get("C_STD_CODE")),
                    "name": r.get("C_C_NAME"),
                    "state": r.get("STATE"),          # 现行 / 废止
                    "nature": r.get("STD_NATURE"),    # 推荐性/强制性
                    "issue_date": r.get("ISSUE_DATE"),
                    "act_date": r.get("ACT_DATE"),
                    "id": r.get("id"),                # 详情页 gbDetailed?id=
                })
            out[code] = rows
            print(f"OK  {code} -> {[(r['code'], r['name'], r['state']) for r in rows[:3]]}")
        except Exception as e:
            out[code] = []
            print(f"ERR {code}: {e}")
    (RAW_DIR / "standards_status.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2))
    return out


if __name__ == "__main__":
    fetch()
