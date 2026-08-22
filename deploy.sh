#!/bin/bash
# 部署: 传镜像+配置 → 服务器 → 重启容器 (ohs-report + caddy)
# 用法: bash deploy.sh
set -e
SRV="ohs"                       # ssh 别名 (ohsdeploy@47.90.137.162)
REMOTE_DIR="$HOME/ohs-report"   # 服务器上 ohsdeploy 可写的目录

echo "=== 1. 传输镜像 (~250MB, 几分钟) ==="
docker save ohs-report:latest | gzip | ssh $SRV 'gunzip | docker load'

echo "=== 2. 上传配置 ==="
ssh $SRV "mkdir -p $REMOTE_DIR/data"
scp docker-compose.yml Caddyfile $SRV:$REMOTE_DIR/
scp .env.deploy $SRV:$REMOTE_DIR/.env

echo "=== 3. 同步数据卷 (保留服务器已有 db/材料) ==="
rsync -av --ignore-existing data/ $SRV:$REMOTE_DIR/data/ || true

echo "=== 4. 重启容器 ==="
ssh $SRV "cd $REMOTE_DIR && docker compose up -d --no-build && docker compose ps"

echo "=== 5. 健康检查 ==="
sleep 3
ssh $SRV "curl -s -o /dev/null -w 'app: %{http_code}\n' http://127.0.0.1:18000/login"
ssh $SRV "curl -s -o /dev/null -w 'https: %{http_code}\n' https://paiyipai.xyz || true"

echo "✅ 部署完成 — https://paiyipai.xyz"
