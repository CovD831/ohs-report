#!/usr/bin/env python3
"""存量清理: 项目正文里**印出来的幽灵标准号** (默认 dry-run, 不写库)

为什么需要 (2026-10-16 取证):
    上游修好之后, 4 份成品里还剩 1 个幽灵号印在纸上 —— c8c7ff0a4d 的 2.5 节正文:
        "…其规模按 GBZ 1—2019 相应分级要求核算" / "依据 GBZ 1—2019 第8章卫生特征分级要求…"
    它既不在一手目录快照里, 也不在 standard_db 里 (读出纠版 `RefFixer` 只认 standard_db
    有登记的号), 所以**纠版链看不见它** —— 这类号是 LLM 当年写稿时编的, 存在 project.data 里。
    护栏 tools/test_std_existence.py 能点名它, 但点名不等于清掉; 存量得有人改。

⚠ 不许"只改标签" (用户明令): 同一句里 `第8章` 也是错的。按我们自己入库的 GBZ 1—2010 规则
    (`gbz1_rule`: 卫生特征分级 = 7.1, 辅助用室/浴室/盥洗 = 7.2.x, 生活用室 = 7.3.x;
     8.x 整章是「应急救援」), 卫生特征分级在**第7章**。所以第 1 条规则连章号一起改,
    第 2 条才处理没有章号的那处。两条都是**整句替换**, 不是把年份擦掉。

判据形状 (与 tools/fix_official_std_codes.py 同一套规矩):
  · 规则表在代码里, 每条带 evidence (一手出处) —— 这是"一次性存量修复", 不是长期改写机制:
    新出现的幽灵号应由 test_std_existence 点名, 再决定是否加规则, 绝不在读路径上挂同义词表;
  · 默认 dry-run, 只打印 前→后 与所在路径; `--apply` 才写 (写的是 project.data, 属业务数据);
  · **反向对表**: 每条规则必须仍命中 ≥1 处, 命中不到就点名 (僵尸规则=以后谁也不知道它管什么);
  · 范围 = 全部项目的 data JSON 里所有字符串叶子 (不按字段名放行, 免得漏掉别处也有的同款)。

用法:
    .venv/bin/python tools/fix_project_std_text.py            # dry-run
    .venv/bin/python tools/fix_project_std_text.py --apply     # 写本地库 (生产需单独批准)
"""
import json
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.paths import DB_PATH  # noqa: E402

# (旧串, 新串, 一手依据)。顺序有意义: 带章号的长句必须先替换, 否则被短规则吃掉一半。
FIXUPS = [
    ("GBZ 1—2019 第8章", "GBZ 1—2010 第7章",
     "gbz1_rule: 7.1=车间卫生特征分级, 7.2.x=辅助用室/浴室/盥洗, 7.3.x=生活用室; 8.x 整章是应急救援"
     " → 年份与章号都错, 必须一起改 (禁止只改标签)"),
    ("GBZ 1—2019", "GBZ 1—2010",
     "acquire/raw/nhc_gbz/gbz_baseline.json: 工业企业设计卫生标准只有 GBZ 1-2010 现行, 无 2019 版"),
    # ↓ 2026-10-16 生产窗口扫出来的两族 (§2 的本地 4 个项目里没有, 所以第一批规则不含它们;
    #   生产实测: 'GBZ 2.2—2019' 25 处/16 个项目, 'GBZ/T 233-2019' 15 处/15 个项目)。
    #   长句先走, 否则被短规则吃掉一半 —— 与上面 GBZ 1 那两条同构。
    ("GBZ 2.2—2019 附录表6", "GBZ 2.2—2007 表6",
     "一手 OCR 记录 knowledge/data/gbz22_physical.json 里 微波辐射 各条 note 一律是 'GBZ 2.2—2007 表6':"
     " 表号 6 对, 但它是 2007 版的**正文表6**, 不是'附录表'; 年份 2019 是自造的 (#17: 目录快照 + std.samr 双查无)"
     " → 年份与'附录'前缀一起改, 不做只换年份 (改完语义仍成立: 表6 就是微波/电磁场的屏蔽与限值依据)"),
    ("GBZ 2.2—2019附录表6", "GBZ 2.2—2007表6",
     "同上, 只是这一批写法不带空格 (生产实测两种排版都有, 分成两条是为了各自保住原排版)"),
    ("GBZ 2.2—2019", "GBZ 2.2—2007",
     "#17 结案: 不存在 'GBZ 2.2—2019' 这个号 (NHC 目录快照与 std.samr 均无), 现行版就是 2007 —— "
     "生产 standard_db 里 5115 行 2026-10-16 已回 state='现行'; 句子里的《…第2部分：物理因素》"
     "与 2007 版标题一致 → 换号不伤语义"),
    ("《工作场所有毒气体检测报警设置规范》（GBZ/T 233-2019）",
     "《工作场所有毒气体检测报警装置设置规范》（GBZ/T 223—2009）",
     "standard_db 登记的是 GBZ/T223-2009《工作场所有毒气体检测报警**装置**设置规范》(实施中); "
     "'GBZ/T 233-2019' 不存在 —— 库里 GBZ/T 233 只有 233-2010《锡矿山工作场所放射卫生防护标准》(主题完全不同)。"
     " 这批句子连标准名都漏了'装置'二字 → **号与名一起改**, 只把 233 改成 223 会留下一个对不上任何标准的名字"),
]


n_hit = {old: 0 for old, _n, _e in FIXUPS}


def apply_fixups(o, path="$", changes=None):
    """递归重建 data JSON: 每个字符串叶子过一遍 FIXUPS → (新对象, [(路径,旧,新,规则,条数)])。

    两条被事故逼出来的规矩:
      · **不能按路径写回**: 键里就有点 (section_states 的键是 "2.5") → 按 . 切分会把 "2.5"
        切成两层, 写回时 KeyError;
      · **每层必须自己组装返回值**: 第一版写成 `{{k: apply_fixups(v, …)}}` —— 把
        `(子对象, changes)` 元组直接塞进了结构, json.dumps 后变成 `[值, changes]` 的嵌套数组,
        c8c7ff0a4d 的 data 从 175KB 涨到 2.6MB。全靠"同一个 changes 数组被反复嵌入"这个
        指纹才把它逆回来 (见 main() 末尾的写回后对表)。
    """
    changes = [] if changes is None else changes
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            out[k], _ = apply_fixups(v, f"{path}.{k}", changes)
        return out, changes
    if isinstance(o, list):
        out = []
        for i, v in enumerate(o):
            nv, _ = apply_fixups(v, f"{path}[{i}]", changes)
            out.append(nv)
        return out, changes
    if not isinstance(o, str):
        return o, changes
    out = o
    for old, new, _ev in FIXUPS:
        k = out.count(old)
        if not k:
            continue
        # 上下文按"这一条规则命中的那一处"取: 先定位再替换, 否则第 2 条规则会拿第 1 条
        #   已经改好的位置当自己的证据 (实测: 两处打印出同一句, 账对不上)。
        i = out.find(old)
        before = out[max(0, i - 24):i + len(old) + 18]
        out = out.replace(old, new)
        after = out[max(0, i - 24):i + len(new) + 18]
        n_hit[old] += k
        changes.append((path, before, after, old, new, k))
    return out, changes


def looks_wrapped(o) -> bool:
    """是不是"元组塞进结构"那种损坏: 某个值是 [x, […]] 形状且第二元素是 changes 数组。"""
    if isinstance(o, dict):
        return any(looks_wrapped(v) for v in o.values())
    if isinstance(o, list):
        if (len(o) == 2 and isinstance(o[1], list) and o[1]
                and all(isinstance(e, list) and len(e) == 6 and isinstance(e[0], str)
                        and e[0].startswith('$') for e in o[1])):
            return True
        return any(looks_wrapped(v) for v in o)
    return False


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    # dry-run 走只读 URI (不碰 -wal/-shm, 也不给验收库开可写连接); --apply 才开可写。
    conn = (sqlite3.connect(DB_PATH) if apply
            else sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True))
    rows = list(conn.execute("SELECT id, data FROM project ORDER BY id"))
    originals = dict(rows)          # 回滚用的原值 (--apply 前就抓在手里)
    edits: dict = {}
    total = 0
    for pid, raw in rows:
        data, changes = apply_fixups(json.loads(raw))
        for path, before, after, old, new, k in changes:
            print(f"  [{pid}] {path}  ({k} 处, 规则 {old!r} → {new!r})")
            print(f"      旧: …{before}…")
            print(f"      新: …{after}…")
        if changes:
            edits[pid] = json.dumps(data, ensure_ascii=False)
            total += sum(c[5] for c in changes)
    print(f"\n命中 {total} 处, 涉及 {len(edits)} 个项目 "
          f"({'--apply: 写库' if apply else 'dry-run: 未写库'})")
    # 反向对表: 规则命中不到 = 僵尸规则 (存量已清完就该删规则, 不许留着当"永久放行")
    zombies = [old for old, _n, _e in FIXUPS if n_hit[old] == 0]
    for old in zombies:
        print(f"  ⓘ 规则未命中 (存量已清完就该删掉它): {old!r}")
    if apply and edits:
        # 写前先把要改的行原值落一份 (/tmp, 不进仓库): 回滚逻辑自己也可能是错的, 留一手。
        dump = Path(tempfile.gettempdir()) / (
            f"ohs_project_data_before_fix_{datetime.now():%Y%m%d_%H%M%S}.json")
        dump.write_text(json.dumps({pid: originals[pid] for pid in edits}, ensure_ascii=False),
                        encoding="utf-8")
        print(f"  ⓘ 写前原值已存 {dump}")
        for pid, raw in edits.items():
            conn.execute("UPDATE project SET data=? WHERE id=?", (raw, pid))
        conn.commit()
        # 写回后对表 (2026-10-16 事故换来的): 第一版把 (对象, changes) 元组塞进了结构,
        #   写进去的就是垃圾。所以"写完必须再读回来验形状", 验不过就**回滚成原值** ——
        #   宁可这个工具报错退出, 也不许它把业务数据改成看不懂的样子。
        bad = []
        for pid, raw in edits.items():
            back = conn.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()[0]
            try:
                obj = json.loads(back)
            except ValueError as e:
                bad.append(f"{pid}: 读回不是合法 JSON ({e})")
                continue
            if not isinstance(obj, dict):
                bad.append(f"{pid}: 读回顶层不是对象 ({type(obj).__name__})")
            elif looks_wrapped(obj):
                bad.append(f"{pid}: 读回里有 [值, changes] 元组残留 (递归写坏了)")
            elif len(back) > len(originals[pid]) + 10:
                bad.append(f"{pid}: 读回字节 {len(back)} > 原值 {len(originals[pid])} "
                           f"(整句替换不该让 data 变大 → 结构被写坏)")
        if bad:
            for pid, orig in originals.items():
                conn.execute("UPDATE project SET data=? WHERE id=?", (orig, pid))
            conn.commit()
            print("  ✗ 写回后对表不过 → 已回滚, 一条都没留:")
            for x in bad:
                print("     ", x)
            conn.close()
            return 1
        print(f"  ✓ 已写 {len(edits)} 个项目的 data, 并逐行读回验过形状")
    conn.close()
    # 退出码: dry-run 有命中 → 1 (提醒还要 --apply); 全部规则都空 → 0
    if not apply and total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
