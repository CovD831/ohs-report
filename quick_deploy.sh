#!/bin/bash
# 快速部署 v2: 源码上送 + 服务器构建 (传输 ~2MB, 不再推 GB级镜像)
# 首次/依赖变更: bash deploy.sh (老方式, 全量镜像)
# 日常改代码:   bash quick_deploy.sh
set -e
set -o pipefail   # 关键: 让管道返回 docker build 的真实退出码 (否则 tail 会掩盖失败)
SRV="ohs"
REMOTE_DIR='~/ohs-report'

echo "=== 1. 打源码包 (排除数据/venv, ~2MB) ==="
tar czf /tmp/ohs_src.tgz --exclude '.git' --exclude '.venv' --exclude 'raw' \
    --exclude 'data' --exclude '__pycache__' --exclude '*.docx' \
    --exclude '.env*' knowledge web acquire requirements.txt Dockerfile docker-compose.yml

echo "=== 2. 上送并解压 ==="
scp -q /tmp/ohs_src.tgz $SRV:/tmp/
ssh $SRV "mkdir -p $REMOTE_DIR && cd $REMOTE_DIR && tar xzf /tmp/ohs_src.tgz && rm /tmp/ohs_src.tgz"

echo "=== 3. 远端构建 (服务器2核, 约5-8分钟) ==="
# --network host: 容器build时Docker bridge出网受限, 必须用host网络才能pip install
ssh $SRV "cd $REMOTE_DIR && docker build --network host -t ohs-report:latest . 2>&1 | tail -4"

echo "=== 4. 重启应用容器 (配置/卷不动) ==="
ssh $SRV "cd $REMOTE_DIR && docker compose up -d --no-deps --force-recreate ohs-report 2>&1 | tail -1"

sleep 3
echo "=== 5. 健康检查 ==="
ssh $SRV "curl -s -o /dev/null -w 'app: %{http_code}\n' http://127.0.0.1:18000/login"
curl -s -o /dev/null -w '公网: %{http_code}\n' https://paiyipai.xyz/login
echo "✅ 完成"
