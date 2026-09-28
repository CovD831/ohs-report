#!/bin/bash
# 快速部署 v2: 源码上送 + 服务器构建 (传输 ~2MB, 不再推 GB级镜像)
# 首次/依赖变更: bash deploy.sh (老方式, 全量镜像)
# 日常改代码:   bash quick_deploy.sh
set -e
set -o pipefail   # 关键: 让管道返回 docker build 的真实退出码 (否则 tail 会掩盖失败)
SRV="ohs"
REMOTE_DIR='~/ohs-report'

echo "=== 1. 打源码包 (排除数据/venv/标准PDF, ~2MB) ==="
# docker-compose.yml 不上送: LLM 端点/key 等运行时配置只在服务器本地维护, 避免部署覆盖
# ⚠ 排除模式必须能匹配**嵌套**目录: `--exclude 'raw'` 只匹配顶层 raw/,
#   抓不到 acquire/raw/ → acquire 有 2.8GB 标准PDF 被整个打包(实测 45MB),
#   传输必断。用 '*/raw' + 'raw' 覆盖任意层级。
# knowledge/ 45MB 是词库/标准数据, 服务器已有, 不随每次部署重传
# (如某次确实改了 knowledge/, 用 --with-knowledge 或不加该 exclude 手工传)
_EXCL=(--exclude '.git' --exclude '.venv' --exclude 'raw' --exclude '*/raw'
       --exclude 'data' --exclude '*/data' --exclude '__pycache__'
       --exclude '*/__pycache__' --exclude '*.docx' --exclude '*.pdf'
       --exclude '.env*' --exclude 'docker-compose.yml')
_SRC=(web tools acquire requirements.txt Dockerfile)
[ "$WITH_KNOWLEDGE" = "1" ] && _SRC=(knowledge "${_SRC[@]}")
tar czf /tmp/ohs_src.tgz "${_EXCL[@]}" "${_SRC[@]}"

_sz=$(du -m /tmp/ohs_src.tgz | cut -f1)
echo "    包大小: ${_sz}MB"
[ "$_sz" -gt 20 ] && echo "  ⚠ 包偏大(${_sz}MB) — 检查是否漏排大目录, 大包易传断"

echo "=== 2. 上送并解压 ==="
# SSH 在本机网络下偶发中途断连 (Connection closed by remote host / lost connection)。
# scp 加保活参数 + 重试, 避免 2MB 传输半途失败要手工重来。
SCP_OPTS="-q -o ConnectTimeout=25 -o ServerAliveInterval=15 -o ServerAliveCountMax=6"
SSH_OPTS="-o ConnectTimeout=25 -o ServerAliveInterval=15 -o ServerAliveCountMax=6"
_ok=0
for _try in 1 2 3; do
  if scp $SCP_OPTS /tmp/ohs_src.tgz $SRV:/tmp/ && \
     ssh $SSH_OPTS $SRV "cd $REMOTE_DIR && tar xzf /tmp/ohs_src.tgz && rm -f /tmp/ohs_src.tgz"; then
    _ok=1; break
  fi
  echo "  ⚠ 上送失败(第 $_try 次), 5s 后重试…"; sleep 5
done
[ "$_ok" = 1 ] || { echo "❌ 源码上送 3 次均失败 — 检查网络/SSH"; exit 1; }

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
