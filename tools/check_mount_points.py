"""排查: _SUB_TABLE_MAP 的挂载小节是否都真实存在于报告结构

问题 (2026-09): 工艺检查表挂 3.5.3, 但 3.5 下只有 3.5.1 → 整表不可达,
导出时静默丢失。需要系统性排查所有挂载点, 而不是修一个算一个。
"""
import sys
from pathlib import Path

ROOT = next((p for p in (Path("/app"), Path(__file__).resolve().parent.parent)
             if (p / "web" / "app.py").exists()), Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))


def all_sections() -> set[str]:
    """报告结构里真实存在的全部小节号

    ⚠ 结构形态: SUBS/SUBS3/SUBS4 的值可能是
       dict  {节号: 标题}  或  list[tuple(节号, 标题)] (如 SUBS[8]=[('8.1','...'),...])
       只取 dict key 会漏掉 list 里的节号 → 假报"挂载点不存在"
    """
    from web.report_struct import SUBS, SUBS3, SUBS4, CHAPTERS  # noqa
    seen: set[str] = set()

    def _eat(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                seen.add(str(k))
                _eat(v)                 # 递归: 值可能是 list/dict
        elif isinstance(obj, (list, tuple)):
            for it in obj:
                # ('8.1', '标题') 形态 → 取第一元素; 嵌套 → 递归
                if isinstance(it, (list, tuple)) and it and isinstance(it[0], str) \
                        and it[0][:1].isdigit():
                    seen.add(it[0])
                else:
                    _eat(it)
        elif isinstance(obj, str) and obj[:1].isdigit() and "." in obj:
            seen.add(obj)

    for d in (SUBS, SUBS3, SUBS4):
        _eat(d)
    for k in (CHAPTERS or {}):
        seen.add(str(k))
    return seen


def ancestor_exists(sub: str, valid: set[str]) -> str | None:
    """返回可回退的父节 (自身不在则回溯父级)"""
    parts = sub.split(".")
    for n in range(len(parts), 0, -1):
        cand = ".".join(parts[:n])
        if cand in valid:
            return cand
    return None


def main():
    from web.word_export import _SUB_TABLE_MAP
    valid = all_sections()
    print(f"报告结构里的小节数: {len(valid)}")
    print(f"_SUB_TABLE_MAP 挂载点: {len(_SUB_TABLE_MAP)}")
    print()
    bad = []
    for sub, m in sorted(_SUB_TABLE_MAP.items()):
        if not m:
            continue
        names = m[1]
        if sub in valid:
            continue
        fb = ancestor_exists(sub, valid)
        bad.append((sub, names, fb))
    if not bad:
        print("✓ 全部挂载点有效")
        return
    print(f"⚠ 挂载点不存在 {len(bad)} 个:")
    for sub, names, fb in bad:
        print(f"  {sub:12} -> {names}")
        print(f"       {'可回退到 ' + fb if fb else '★ 无可用父节!'}")
    print()
    print("受影响的表:", [n for _, ns, _ in bad for n in ns])


if __name__ == "__main__":
    main()
