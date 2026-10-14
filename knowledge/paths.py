"""数据库路径的唯一定义 (S1a′, 2026-10-15)

为什么放 knowledge/ 而不是 web/: 这个常量描述的是**数据文件**本身, 而
`knowledge/oel.py` 已经持有它的 DDL 与工厂; `web/` 反过来 import 它 (web 早已被
knowledge 反向引用, 如 knowledge/project_assess.py:213 → web.hazard_grid)。
放这里可以让"路径只有一个产地"这条规则不需要新顶层包, 也不动 Dockerfile。

守门口径 (tools/test_dataset_door.py):
  · `data/ohs.db` 字面量只许出现在本文件;
  · web/ 与 knowledge/ 里 `sqlite3.connect(` 只许出现在工厂 (本模块与 knowledge/oel.py)
    或 `if __name__ == "__main__":` 开发块内 —— 谓词按"是否在 __main__ 块内"判,
    不按文件判 (v1 计划按文件判会整批放过生产函数里的裸连)。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "ohs.db"


def db_uri(read_only: bool = False) -> str:
    """SQLite URI 形式。只读连接必须走这里 —— `mode=ro` 不会创建文件, 缺库会直接报错,
    而 `sqlite3.connect('data/ohs.db')` 那种写法会**静默新建空库** (oel.connect 还 mkdir),
    这正是今天从错误 cwd 跑脚本会得到"空数据、报告全是待补充"的根因。"""
    return f"file:{DB_PATH}?mode=ro" if read_only else str(DB_PATH)
