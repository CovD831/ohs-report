# 职业病危害预评价报告工作台 — 生产镜像
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120 -r requirements.txt

COPY knowledge/ knowledge/
COPY web/ web/
COPY tools/ tools/
# quick_deploy.sh / Dockerfile 必须进镜像 (2026-10-16 实测两连):
#   ① tools/chain_audit.py 的判据范围是**从 quick_deploy.sh 的两条数组读出来的** (部署链唯一
#      真源), 而 test_dataset_door T8 在容器里跑 —— 镜像里没有这个文件时 T8 直接
#      FileNotFoundError, 守卫套在第 4 步整条崩 (按 COPY 集裁一棵树复现过)。
#   ② T9 的交付链三方对表 (部署名单 ↔ MD5_FILES ↔ 镜像 COPY 集) 读的源头就是这份 Dockerfile;
#      它不在镜像里时, 同一段在容器里只能报 93 条"镜像里没有 X"这种无地自证的红。
# 两个文件都不含机密 (SRV 是 ssh 别名, 不含 IP/密钥), 且本来就在仓库里。
COPY quick_deploy.sh .
COPY Dockerfile .
# 官方数据层版本戳 manifest: 容器里用它比对**挂载进来的生产库** (tools/data_stamp.py --check)
COPY data_stamp.json .
# 标准号"待复核/豁免"登记表 (2026-10-16 从官方层表搬成随代码走的 JSON, 用户拍板: 官方层只装
# 官方来源数据)。为什么不在 knowledge/ 下: _KN_EXCL 排 'knowledge/*.json' → 打包时会被静默丢掉。
# 缺文件时 content_hold()/load_exempt() 是**具名抛错**(StdConfigMissing / SystemExit), 不静默当空集
# —— 空集=「没有待复核项」=把旧判据刷上新编号, 正是这份登记要防的事。
COPY std_render_hold.json std_code_exempt.json ./
# acquire 仅需 base (数据采集模块, 部署机不采集也保留结构)
COPY acquire/ acquire/

# data/ 由 volume 挂载 (ohs.db + materials + 导出报告), 不打进镜像
# network_mode: host 时直接监听 18000 (nginx 已反代该端口; bridge端口映射失效)
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "18000", "--workers", "2"]
