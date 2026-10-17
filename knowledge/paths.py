"""数据目录与数据库路径的**唯一产地** (S1a′ 立, S1c′ D0 补全, 2026-10-15)

为什么放 knowledge/ 而不是 web/: 这些常量描述的是**数据文件**本身, 而
`knowledge/oel.py` 已经持有它的 DDL 与工厂; `web/` 反过来 import 它 (web 早已被
knowledge 反向引用, 如 knowledge/project_assess.py:213 → web.hazard_grid)。
放这里可以让"路径只有一个产地"这条规则不需要新顶层包, 也不动 Dockerfile。

D0 为什么要把 materials / vision_cache / .secret 也收进来 (而不是只管 DB_PATH):
  原状是 `DB_PATH` 有产地, 其余各自 `DB.parent / "materials"`、
  `Path(__file__).parent.parent/"data"/"materials"`、`_data_dir()/"vision_cache"`,
  三把尺子各算各的。**只要有人把库指到别处而材料目录没跟着走**, `cleanup_expired()`
  就会按"库里没有此行"把真实企业材料目录 rmtree 掉 (本地 data/materials = 193MB)。
  现在它们全部派生自同一个 DATA_DIR: 改位置只能改这一处, 结构与数据不可能再错配。
  `OHS_DATA_DIR` 环境变量是唯一的整体搬家开关 (容器挂载用绝对路径时可不依赖它)。

守门口径 (tools/test_dataset_door.py):
  · `data/ohs.db` 字面量只许出现在本文件;
  · `data/materials`、`data/vision_cache` 字面量只许出现在本文件 (D0 新增);
  · web/ 与 knowledge/ 里 `sqlite3.connect(` 只许出现在工厂 (本模块与 knowledge/oel.py)
    或 `if __name__ == "__main__":` 开发块内 —— 谓词按"是否在 __main__ 块内"判,
    不按文件判 (v1 计划按文件判会整批放过生产函数里的裸连)。

S1c′ D4 (2026-10-16) 再加两件:
  · `STARTUP_LOCK_FILE` —— 启动期串行锁的文件, 与库同源搬家;
  · `startup_lock()` —— 它是"多 worker 并发跑启动准备"的唯一串行点, 见其文档。
"""
import os
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    """数据目录唯一定义。允许用 OHS_DATA_DIR 整体搬家 (库+材料+缓存一起走)。"""
    v = os.getenv("OHS_DATA_DIR", "").strip()
    return Path(v).resolve() if v else ROOT / "data"


DATA_DIR = _data_dir()
DB_PATH = DATA_DIR / "ohs.db"
MATERIALS_DIR = DATA_DIR / "materials"
VISION_CACHE_DIR = DATA_DIR / "vision_cache"
SECRET_FILE = DATA_DIR / ".secret"
STARTUP_LOCK_FILE = DATA_DIR / ".startup.lock"

# 两份**随代码走**的登记表 (2026-10-16, 用户拍板"移出官方层改 JSON"):
#   · std_render_hold.json —— "编号已更新但判据内容尚未按新版复核", 影响印出的标签 → 属应用配置;
#   · std_code_exempt.json —— 校验豁免, 只被守卫读 → 属验收配置。
# 为什么不进 `data/`: 那是可搬家/可被 OHS_DATA_DIR 指到别处的运行期数据, 而这两份必须与代码同版本
# 部署 (改它=改行为, 要走 git 与镜像)。为什么不进 `knowledge/`: 打包时 `--exclude 'knowledge/*.json'`
# 会把 knowledge 下的 json 全排掉 (2026-10 的 region_facts.json 事故就是这么来的: 本地全绿、
# 容器里读不到 → 静默空转)。放仓库根 = 和 data_stamp.json 同一条 COPY/MD5 通路, 由
# tools/chain_audit.delivery_chain_gaps() 逐条对表"在包里 + 在镜像里"。
STD_RENDER_HOLD_FILE = ROOT / "std_render_hold.json"
STD_CODE_EXEMPT_FILE = ROOT / "std_code_exempt.json"


class StartupLockHeld(RuntimeError):
    """启动锁在超时内没拿到。具名, 因为"另一个进程正在准备数据"必须能被区分出来,
    而不是混进一句通用的启动失败里。"""


def _lock_timeout() -> float:
    v = os.getenv("OHS_STARTUP_LOCK_TIMEOUT", "").strip()
    return float(v) if v else 60.0


@contextmanager
def startup_lock(timeout: float | None = None):
    """跨进程串行化"启动期准备", 供 web/app.py:_bootstrap() 整段包住自己。

    为什么需要它 (2026-10-16 本地部署演练 tools/local_deploy_drill.py 实测):
    生产 CMD 是 `uvicorn web.app:app --workers 2`, **两个进程各跑一遍 lifespan**。
    空 volume 首启时 `init_users()` 的形状是"先查有没有用户 → 没有就插 admin"
    (check-then-act), 两边都查到空表、都去插, 输的那个抛
    `sqlite3.IntegrityError: UNIQUE constraint failed: users.username`
    → uvicorn 打 `Application startup failed. Exiting.` → **服务静默少一个 worker**,
    只在日志里留一行 traceback。同类形状还有 `project.owner_id` 迁移、`.secret` 首生成。

    修在"整段串行"而不是"逐个函数打补丁": 以后新增的启动步骤自动被覆盖,
    不需要每个人都记得给自己的函数加幂等 (这才是通用)。
    超时后**响**而不是继续裸跑 —— 拿不到锁还接着写库, 就回到了并发写库的原始问题。

    fcntl 在函数内 import: 本项目只跑 Linux/macOS, 但 paths.py 被全库 import,
    不该让一个 Windows 用户在模块加载期就崩在这里。
    """
    import fcntl
    import time as _t
    limit = _lock_timeout() if timeout is None else timeout
    STARTUP_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(STARTUP_LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = _t.time() + limit
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if _t.time() >= deadline:
                    raise StartupLockHeld(
                        f"{limit:.0f}s 内没拿到启动锁 {STARTUP_LOCK_FILE}: 另一个进程正在跑"
                        f"启动准备 (或它卡住了)。宁可启动失败, 也不在未串行化时写库。")
                _t.sleep(0.2)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)



def same_origin(p: Path) -> bool:
    """p 是否落在本进程 DATA_DIR 之下 (解析符号链接后判)。
    删除类操作 (cleanup_expired 的 rmtree) 用它做最后一道闸 —— 判据不写"长得像",
    而是写 resolve() 之后是不是同一个子树, 因为错配恰恰来自"名字对、位置不对"。"""
    try:
        Path(p).resolve().relative_to(DATA_DIR.resolve())
        return True
    except (ValueError, OSError):
        return False


def db_uri(read_only: bool = False) -> str:
    """SQLite URI 形式。只读连接必须走这里 —— `mode=ro` 不会创建文件, 缺库会直接报错,
    而 `sqlite3.connect('data/ohs.db')` 那种写法会**静默新建空库** (oel.connect 还 mkdir),
    这正是今天从错误 cwd 跑脚本会得到"空数据、报告全是待补充"的根因。"""
    return f"file:{DB_PATH}?mode=ro" if read_only else str(DB_PATH)
