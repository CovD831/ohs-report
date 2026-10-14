#!/bin/bash
# 快速部署 v2: 源码上送 + 服务器构建 (传输 ~2MB, 不再推 GB级镜像)
# 首次/依赖变更: bash deploy.sh (老方式, 全量镜像)
# 日常改代码:   bash quick_deploy.sh
set -e
set -o pipefail   # 关键: 让管道返回 docker build 的真实退出码 (否则 tail 会掩盖失败)
SRV="ohs"
REMOTE_DIR='~/ohs-report'

# 校验清单 (第 1 步"必须在包里" 与第 6 步"必须进容器" 共用同一份, 避免两处漂移)
# ⚠⚠ 教训 (2026-10): 清单必须覆盖**本轮改过的文件** —— 只列固定几个文件,
#   改了别的文件就校验不到 = 等于没验。
MD5_FILES=(web/app.py knowledge/project_assess.py knowledge/gbz1_loader.py knowledge/ppe_vertical.py
           web/table_skeleton.py web/section_filler.py web/section_style_spec.json web/word_export.py
           web/llm_draft.py web/standard_version.py web/orig_table_map.py web/report_parser.py
           web/structure_data.py web/table_builder.py web/hazard_grid.py web/report_struct.py
           web/unit_gen.py web/thi_rating.py web/validators.py web/derived_fields.py
           web/field_projection.py tools/extract_mgmt_facts.py tools/patch_missing_current_std.py
           tools/compare_real_report.py tools/data_stamp.py tools/fix_official_std_codes.py
           tools/test_std_currentize.py tools/test_std_ref_parity.py data_stamp.json
           tools/export_fingerprint.py tools/report_bench.py tools/test_gate_sensitivity.py
           tools/golden/ada54603a9.fp.json.gz tools/golden/c8c7ff0a4d.fp.json.gz
           tools/golden/2863a36451.fp.json.gz tools/golden/57ffa2b3b0.fp.json.gz)

# 容器内守卫套 (第 4 步, 切流量之前跑): 必须选**与项目业务数据无关**的测试,
# 否则生产库一有差异就误判成"部署坏了"。选取口径见 tools/data_stamp.py 同款注释。
GUARD_TESTS=(test_std_ref_parity test_std_currentize test_std_supersede test_std_guard
             test_toxicity_cell test_toxicity_match test_unfreeze_cells test_v01_staffing
             test_ppe_vertical test_must_cover_parity test_export_payload_parity
             test_number_provenance test_health_table_header test_front_matter
             test_gate_sensitivity)

# ⚠⚠ 教训 (2026-10-14): 部署中途 scp 连续 "Connection closed", 排查确认是**本机源 IP
#   被服务器 fail2ban 全 TCP 封禁** (ICMP 仍通, 换 IP 立刻可连)。根因不是网络抖动,
#   而是本脚本的连接数: v2 第 6 步每个文件单起一次 ssh (24 个文件) + 前置各步 ≈ 30 次,
#   两次失败重试就 60+ 次 —— 恰好命中 fail2ban 的 ssh 失败/频率阈值。
#   正解: **全程复用一条 TCP 连接** (ControlMaster), 一轮部署 = 1 次真实握手;
#   并且第 6 步把逐文件 ssh 收成一次 ssh (远端一条 md5sum 列全部文件)。
#   诊断口径: 若仍被封, 先看 `ssh -G $SRV | grep controlpath` 与 ~/.ssh/cm-* 是否存在。
_CM="${HOME}/.ssh/cm-ohs-deploy"
SSH_BASE=(-o ConnectTimeout=25 -o ServerAliveInterval=15 -o ServerAliveCountMax=6
          -o ControlMaster=auto -o "ControlPath=${_CM}" -o ControlPersist=900)
ssh_run() { ssh "${SSH_BASE[@]}" "$SRV" "$@"; }
scp_run() { scp "${SSH_BASE[@]}" "$@"; }
_close_master() { ssh -o "ControlPath=${_CM}" -O exit "$SRV" 2>/dev/null || true; }
trap _close_master EXIT

echo "=== 0. 建立唯一一条复用连接 ==="
# 先握手一次: 若已被封/密钥有问题, 在第 0 步就退出, 不会留下"传了一半"的部署状态
if ! ssh "${SSH_BASE[@]}" -f -N "$SRV" 2>/tmp/ohs_cm.err; then
  echo "❌ 无法连接 $SRV — 先看 /tmp/ohs_cm.err (若是 Connection closed by ... 大概率 IP 被封)"
  tail -2 /tmp/ohs_cm.err
  exit 1
fi
echo "    复用连接已建立 (ControlPath=${_CM})"


echo "=== 1. 打源码包 (排除数据/venv/标准PDF, ~1-2MB) ==="
# docker-compose.yml 不上送: LLM 端点/key 等运行时配置只在服务器本地维护, 避免部署覆盖
# ⚠ 排除模式必须能匹配**嵌套**目录: `--exclude 'raw'` 只匹配顶层 raw/,
#   抓不到 acquire/raw/ → acquire 有 2.8GB 标准PDF 被整个打包(实测 43MB),
#   传输必断。用 '*/raw' + 'raw' 覆盖任意层级。
# ⚠⚠ 教训 (2026-09): knowledge/ 曾整目录排除 → 改了 knowledge/project_assess.py
#   部署后服务器上还是旧代码, 而本地测试全过 = "部署了但没生效" 的静默假成功。
#   正解: knowledge 只送 **.py 源码** (小), 排除它的数据文件 (*.json/*.csv 大)。
# ⚠⚠ 教训 (2026-10): _KN_EXCL 里排 '*.json' 是给 knowledge/ 用的,
#   但它作用在**整个 tar**(web+knowledge+tools), 把 web/section_style_spec.json
#   也一起排掉了 → 改了样式规格部署后容器里还是旧的 = 静默假成功。
#   正解: 排除规则一律用 **路径限定** (--exclude 'knowledge/*.json'), 不用裸 '*.json'。
_EXCL=(--exclude '.git' --exclude '.venv' --exclude 'raw' --exclude '*/raw'
       --exclude 'data' --exclude '*/data' --exclude '__pycache__'
       --exclude '*/__pycache__' --exclude '*.docx' --exclude '*.pdf'
       --exclude '.env*' --exclude 'docker-compose.yml')
# knowledge: 只要代码 (小), 排除大体积数据/词库/扫描页图片
#   gbz22_pages 33M + gb39800_pages 12M = 标准扫描页 PNG (OCR 中间产物, 服务器不需要)
_KN_EXCL=(--exclude 'knowledge/*.json' --exclude 'knowledge/*.csv' --exclude 'knowledge/*.txt'
          --exclude 'knowledge/*.xlsx'
          --exclude 'knowledge/*.png' --exclude 'knowledge/*.jpg' --exclude 'knowledge/*.pdf'
          --exclude '*/gbz22_pages' --exclude '*/gb39800_pages' --exclude '*/__pycache__')
_SRC=(web tools acquire knowledge requirements.txt Dockerfile data_stamp.json)
tar czf /tmp/ohs_src.tgz "${_EXCL[@]}" "${_KN_EXCL[@]}" "${_SRC[@]}"

_sz=$(du -m /tmp/ohs_src.tgz | cut -f1)
echo "    包大小: ${_sz}MB"
[ "$_sz" -gt 20 ] && echo "  ⚠ 包偏大(${_sz}MB) — 检查是否漏排大目录, 大包易传断"

# 部署前校验: 关键源码目录必须在包里 (防"漏送目录"类静默失败)
# audit_numbers.py 只做"在包里"校验: 容器内是否有该文件取决于 Dockerfile, 不参与 md5 比对
for _must in "${MD5_FILES[@]}" tools/audit_numbers.py; do
  if ! tar tzf /tmp/ohs_src.tgz "$_must" >/dev/null 2>&1; then
    echo "❌ 包内缺少 $_must — 打包规则有误, 中止"; exit 1
  fi
done
echo "    包内容校验 OK (web/knowledge/tools 齐)"

echo "=== 2. 上送并解压 ==="
# 重试逻辑保留, 但重试**不再新建 TCP** (复用第 0 步的主连接)。
# v2 的问题正是"每次失败重试 = 再握手一次", 把偶发断连放大成 fail2ban 触发条件。
_ok=0
for _try in 1 2 3; do
  if scp_run -q /tmp/ohs_src.tgz $SRV:/tmp/ && \
     ssh_run "cd $REMOTE_DIR && tar xzf /tmp/ohs_src.tgz && rm -f /tmp/ohs_src.tgz"; then
    _ok=1; break
  fi
  echo "  ⚠ 上送失败(第 $_try 次), 5s 后重试…"; sleep 5
done
[ "$_ok" = 1 ] || { echo "❌ 源码上送 3 次均失败 — 检查网络/SSH"; exit 1; }

echo "=== 3. 远端构建 (服务器2核, 约5-8分钟) ==="
# --network host: 容器build时Docker bridge出网受限, 必须用host网络才能pip install
# 构建期间连接空闲 ~8 分钟: 靠 ServerAliveInterval=15 保活 (v2 无保活, 长构建常被断)
ssh_run "cd $REMOTE_DIR && docker build --network host -t ohs-report:latest . 2>&1 | tail -4"

echo "=== 4. 新镜像守卫套 (切流量之前, 一次性容器) ==="
# 为什么放在重启之前: md5 一致只证明"代码送进去了", 不证明"在新镜像的运行期环境里跑得动"
#   (依赖缺失/数据文件缺失/Python 版本差异都只有容器内才暴露;
#    2026-10 的 region_facts.json 事故就是本地全绿、生产静默空转)。
# 用 --rm 一次性容器 + 挂生产数据卷: 现网容器不动, 守卫红了就中止, 不留半部署。
_g_fail=0
_gl=$(mktemp)
for _t in "${GUARD_TESTS[@]}"; do
  # 退出码必须由 ssh 本身给出: 远端接 `| tail` 会让管道返回 tail 的码 = 假绿
  if ssh_run "cd $REMOTE_DIR && docker run --rm -v \$HOME/ohs-report/data:/app/data ohs-report:latest python tools/$_t.py" > "$_gl" 2>&1; then
    echo "  ✓ $_t"
  else
    echo "  ✗ $_t 容器内失败, 末 8 行:"
    tail -8 "$_gl"
    _g_fail=1
  fi
done
rm -f "$_gl"
[ "$_g_fail" = 1 ] && { echo "❌ 守卫套在容器内未通过 — 现网容器保持旧版, 未切换"; exit 1; }

echo "=== 5. 重启应用容器 (配置/卷不动) ==="
ssh_run "cd $REMOTE_DIR && docker compose up -d --no-deps --force-recreate ohs-report 2>&1 | tail -1"

sleep 3
echo "=== 6. 健康检查 + 部署内容校验 ==="
ssh_run "curl -s -o /dev/null -w 'app: %{http_code}\n' http://127.0.0.1:18000/login"
curl -s -o /dev/null -w '公网: %{http_code}\n' https://paiyipai.xyz/login

# ⚠ 关键: 校验"新代码真的进容器了" (防"部署成功但代码是旧的"静默假成功)
# 比对本地与容器内关键文件的 md5 (清单 = 顶部 MD5_FILES)
# ⚠⚠ 教训 (2026-10): 清单必须覆盖**本轮改过的文件** —— 只列固定几个文件,
#   改了别的文件就校验不到 = 等于没验。每轮改动后把新文件并进 MD5_FILES。
echo "=== 7. 源码一致性校验 (本地 vs 容器, 单条命令) ==="
# v2 这里是"每个文件一次 ssh" → 24 次握手, 就是 fail2ban 的根因。
# 改成: 远端一条 md5sum 吃全部文件, 结果落本地临时文件后再逐个比。
_paths=$(for _f in "${MD5_FILES[@]}"; do printf '/app/%s ' "$_f"; done)
_rmd5=$(mktemp)
if ! ssh_run "docker exec ohs-report md5sum $_paths" > "$_rmd5" 2>/dev/null; then
  echo "  ⚠ 容器内有文件读不到 (缺文件或容器未起), 明细见下"
fi
_mismatch=0
for _f in "${MD5_FILES[@]}"; do
  _l=$(md5 -q "$_f" 2>/dev/null || md5sum "$_f" | cut -d' ' -f1)
  _r=$(awk -v p="/app/$_f" '$2==p{print $1; exit}' "$_rmd5")
  if [ -n "$_l" ] && [ "$_l" = "$_r" ]; then
    echo "  ✓ $_f"
  else
    echo "  ✗ $_f 不一致 (本地 ${_l:-无} / 容器 ${_r:-无}) — 该文件未生效!"
    _mismatch=1
  fi
done
rm -f "$_rmd5"
[ "$_mismatch" = 1 ] && { echo "❌ 有源码未同步到容器 — 检查打包/构建"; exit 1; }

echo "=== 8. 官方数据层版本戳 (生产库 vs 本地 manifest, 只报不拦) ==="
# 为什么需要: 部署刻意排除 data/, md5 只管代码 → 官方数据层(限值/名录/标准库)漂移无凭据。
#   2026-10-14 实测: 本地 hazard_toxicity 104 行 / 生产 103 行, 本地绿的守卫在生产崩。
# 只报不拦: 生产数据本就允许与本地不同 (补录默认只写本地), 但必须**知道**差在哪。
ssh_run "cd $REMOTE_DIR && docker exec ohs-report python tools/data_stamp.py --check /app/data_stamp.json" \
  || echo "  ⚠ 上面是官方数据层与本地 manifest 的差异 — 若本轮改动依赖这些数据, 结论不能直接套到生产"

echo "✅ 完成"
