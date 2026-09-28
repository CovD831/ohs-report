#!/bin/bash
# 快速部署 v2: 源码上送 + 服务器构建 (传输 ~2MB, 不再推 GB级镜像)
# 首次/依赖变更: bash deploy.sh (老方式, 全量镜像)
# 日常改代码:   bash quick_deploy.sh
set -e
set -o pipefail   # 关键: 让管道返回 docker build 的真实退出码 (否则 tail 会掩盖失败)
SRV="ohs"
REMOTE_DIR='~/ohs-report'

echo "=== 1. 打源码包 (排除数据/venv/标准PDF, ~1-2MB) ==="
# docker-compose.yml 不上送: LLM 端点/key 等运行时配置只在服务器本地维护, 避免部署覆盖
# ⚠ 排除模式必须能匹配**嵌套**目录: `--exclude 'raw'` 只匹配顶层 raw/,
#   抓不到 acquire/raw/ → acquire 有 2.8GB 标准PDF 被整个打包(实测 43MB),
#   传输必断。用 '*/raw' + 'raw' 覆盖任意层级。
# ⚠⚠ 教训 (2026-09): knowledge/ 曾整目录排除 → 改了 knowledge/project_assess.py
#   部署后服务器上还是旧代码, 而本地测试全过 = "部署了但没生效" 的静默假成功。
#   正解: knowledge 只送 **.py 源码** (小), 排除它的数据文件 (*.json/*.csv 大)。
_EXCL=(--exclude '.git' --exclude '.venv' --exclude 'raw' --exclude '*/raw'
       --exclude 'data' --exclude '*/data' --exclude '__pycache__'
       --exclude '*/__pycache__' --exclude '*.docx' --exclude '*.pdf'
       --exclude '.env*' --exclude 'docker-compose.yml')
# knowledge: 只要代码 (小), 排除大体积数据/词库/扫描页图片
#   gbz22_pages 33M + gb39800_pages 12M = 标准扫描页 PNG (OCR 中间产物, 服务器不需要)
_KN_EXCL=(--exclude '*.json' --exclude '*.csv' --exclude '*.txt' --exclude '*.xlsx'
          --exclude '*.png' --exclude '*.jpg' --exclude '*.pdf'
          --exclude '*/gbz22_pages' --exclude '*/gb39800_pages' --exclude '*/__pycache__')
_SRC=(web tools acquire knowledge requirements.txt Dockerfile)
tar czf /tmp/ohs_src.tgz "${_EXCL[@]}" "${_KN_EXCL[@]}" "${_SRC[@]}"

_sz=$(du -m /tmp/ohs_src.tgz | cut -f1)
echo "    包大小: ${_sz}MB"
[ "$_sz" -gt 20 ] && echo "  ⚠ 包偏大(${_sz}MB) — 检查是否漏排大目录, 大包易传断"

# 部署前校验: 关键源码目录必须在包里 (防"漏送目录"类静默失败)
for _must in web/app.py knowledge/project_assess.py tools/audit_numbers.py; do
  if ! tar tzf /tmp/ohs_src.tgz "$_must" >/dev/null 2>&1; then
    echo "❌ 包内缺少 $_must — 打包规则有误, 中止"; exit 1
  fi
done
echo "    包内容校验 OK (web/knowledge/tools 齐)"

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
echo "=== 5. 健康检查 + 部署内容校验 ==="
ssh $SRV "curl -s -o /dev/null -w 'app: %{http_code}\n' http://127.0.0.1:18000/login"
curl -s -o /dev/null -w '公网: %{http_code}\n' https://paiyipai.xyz/login

# ⚠ 关键: 校验"新代码真的进容器了" (防"部署成功但代码是旧的"静默假成功)
# 比对本地与容器内关键文件的 md5
echo "=== 6. 源码一致性校验 (本地 vs 容器) ==="
_mismatch=0
for _f in web/app.py knowledge/project_assess.py web/table_skeleton.py; do
  _l=$(md5 -q "$_f" 2>/dev/null || md5sum "$_f" | cut -d' ' -f1)
  _r=$(ssh $SRV "docker exec ohs-report md5sum /app/$_f 2>/dev/null | cut -d' ' -f1")
  if [ "$_l" = "$_r" ]; then
    echo "  ✓ $_f"
  else
    echo "  ✗ $_f 不一致 (本地 $_l / 容器 $_r) — 该文件未生效!"
    _mismatch=1
  fi
done
[ "$_mismatch" = 1 ] && { echo "❌ 有源码未同步到容器 — 检查打包/构建"; exit 1; }
echo "✅ 完成"
