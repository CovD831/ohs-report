# 职业病危害预评价报告工作台 — 生产镜像
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120 -r requirements.txt

COPY knowledge/ knowledge/
COPY web/ web/
COPY tools/ tools/
# acquire 仅需 base (数据采集模块, 部署机不采集也保留结构)
COPY acquire/ acquire/

# data/ 由 volume 挂载 (ohs.db + materials + 导出报告), 不打进镜像
# network_mode: host 时直接监听 18000 (nginx 已反代该端口; bridge端口映射失效)
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "18000", "--workers", "2"]
