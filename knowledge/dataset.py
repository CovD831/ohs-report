"""Dataset: 官方数据层的唯一读出入口 (S1b′, 2026-10-15)

为什么要有这个文件
------------------
"读出即纠版" 这道护栏 (废止标准号不得上纸) 判据只有一份 (knowledge.standard_version),
但**要不要过它**过去散落在各站点的 `conn.execute(...)` 里: 白名单 37 处读点中只有 22 处
走过 rows_of, 其余直接裸读; 同一张表的同一列在两个文件里可以一个纠版一个不纠版, 而且
从代码上看不出这是有意还是漏了。本模块把出口收成两个, 并把"有意不纠版"变成必须写理由的
显式表态:

  rows()/one()   读出即纠版 (官方层文本里的废止标准号 → 现行版)。报告正文/表格取数走这里。
  raw()/raw_one() 原样读出, 必须给 reason。只用于"纠版会把判据本身吃掉"的读点。

raw 的合法用途 (W3 取证, 别扩大):
  - `standard_db` 自身: 库里 2222 行有 **178 行的 code 被 is_superseded 判为废止**
    (旧版号本来就该原样存在, 它们是被代替的那一侧)。让它走纠版读口 = 把 code/replace_of
    改成别的号 → 替代关系反查表静默失效, 全库判据一起坏。
  - 因子名/CAS/类别这类**不含标准号**的取数: 走纠版不会更错, 但也不改变任何东西,
    为了口径诚实同样 raw + 理由。

连接与生命周期
--------------
- conn 由调用方注入 (只读用 knowledge.oel.connect_ro()); 本门不建连接、不跑 DDL、不写库。
- 一次导出/一个工作单元一个 Dataset 实例。判据缓存 (standard_db 快照 + code→现行版 memo)
  按"连接对象 + PRAGMA data_version"复用, 与实例寿命无关, 所以实例可以放心地就近构造;
  但别跨请求长期挂着 —— 它持着连接。
- 写侧工具改完 standard_db 要调 knowledge.standard_version.invalidate_caches()。

守卫
----
tools/test_std_currentize.py C 段 (AST): 官方规则表的标准号列不得用裸 `*.execute()` 读;
raw/raw_one 必须带非空 reason 字面量; 纠版出口不得读 standard_db 的 code/replace_of。
tools/test_door_currentize_unit.py: 合成库上证明两个出口语义真的不同 + 记忆化生效 +
他连接原地改写后可见。
"""
from __future__ import annotations

import sqlite3

from knowledge.standard_version import currentizer


class Dataset:
    """官方数据层读门: rows/one = 纠版出口, raw/raw_one = 原样出口 (须写理由)。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        # 判据在实例构造时取一次 (内含 data_version 新鲜度校验), 之后逐格复用。
        # 为什么不在每次读时校验: 导出对"表内每格"都过一次判据 (实测单项目 ~6.8 万格),
        # 每格一条 PRAGMA 会把一次导出的 SQL 数从 ~4 千抬到 ~10 万 —— 校验放在
        # "实例 = 一个工作单元" 这个粒度上, 正是本门的生命周期约定。
        self._f = currentizer(conn)

    # ── 纠版出口 (报告取数) ────────────────────────────────────────────────
    def rows(self, sql: str, args=()) -> list[tuple]:
        """查询 → 行集, 每个文本单元格先过标准号兜底 (非字符串列原样)。"""
        return [tuple(self._f.cell(v) for v in r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> tuple | None:
        """同 rows(), 取第一行; 无行返回 None (与 fetchone() 口径一致)。"""
        r = self.conn.execute(sql, args).fetchone()
        return None if r is None else tuple(self._f.cell(v) for v in r)

    def fix(self, text: str) -> tuple[str, list[str]]:
        """任意文本 (多为 LLM 叙述/表内单元格) → 纠版后的文本 + 改动说明。

        与 rows()/one() 共用同一份判据, 不再另起一条"文字里的标准号"专用路径。
        """
        return self._f.fix(text)

    # ── 原样出口 (判据自身 / 与标准号无关的取数) ─────────────────────────────
    def raw(self, sql: str, args=(), *, reason: str) -> list[tuple]:
        """原样读出, **不做**标准号兜底。reason 必须写清为什么这条路不该纠版。"""
        self._check_reason("raw", reason)
        return self.conn.execute(sql, args).fetchall()

    def raw_one(self, sql: str, args=(), *, reason: str) -> tuple | None:
        """同 raw(), 取第一行。"""
        self._check_reason("raw_one", reason)
        return self.conn.execute(sql, args).fetchone()

    @staticmethod
    def _check_reason(exit_name: str, reason: str) -> None:
        # 运行时就拦一次: 守卫只在提交时响, 空 reason 的 raw 一旦被跑到就已经在漏了
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                f"Dataset.{exit_name} 必须带 reason: 这条读口不纠版的依据是什么 "
                f"(拿不准就改用 rows()/one())")
