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
           web/llm_draft.py knowledge/standard_version.py web/orig_table_map.py web/report_parser.py
           web/structure_data.py web/table_builder.py web/hazard_grid.py web/report_struct.py
           web/unit_gen.py web/thi_rating.py web/validators.py web/derived_fields.py
           web/field_projection.py tools/extract_mgmt_facts.py tools/patch_missing_current_std.py
           tools/compare_real_report.py tools/data_stamp.py tools/fix_official_std_codes.py
           tools/test_std_currentize.py tools/test_std_ref_parity.py data_stamp.json
           tools/export_fingerprint.py tools/report_bench.py tools/test_gate_sensitivity.py
           tools/gate_coverage.py tools/test_gate_coverage_selftest.py
           tools/golden/gate_coverage.json tools/golden/gate_probe.json
           tools/golden/ada54603a9.fp.json.gz tools/golden/c8c7ff0a4d.fp.json.gz
           tools/golden/2863a36451.fp.json.gz tools/golden/57ffa2b3b0.fp.json.gz
           tools/golden/gate_columns.json tools/golden/gate_migration_list.json
           knowledge/paths.py knowledge/oel.py web/auth.py web/tasks.py web/projects_db.py
           web/std_guard.py web/structure_data.py web/external_tables.py web/migrate_states.py
           knowledge/verify_standard_db.py knowledge/evidence_engine.py
           knowledge/standard_db_loader.py knowledge/fix_mapping.py
           tools/test_dataset_door.py knowledge/dataset.py web/entity_link.py
           knowledge/judge_cli.py knowledge/process_material_extractor.py
           knowledge/check_result_templates.py knowledge/seed_aux_unit.py
           tools/test_door_currentize_unit.py web/uploads.py web/vision_extract.py
           tools/test_import_purity.py tools/export_e2e.py
           web/validators.py web/unit_gen.py web/subsection_gen.py web/paragraph_gen.py
           web/compare_full.py web/batch_material_deep.py knowledge/ppe_matrix_loader.py
           tools/chain_audit.py tools/db_writers.py tools/test_acceptance_readonly.py
           tools/local_deploy_drill.py quick_deploy.sh Dockerfile
           # 2026-10-16 (S1c″ D4′): 守卫套 21 个里有 12 个不在清单 —— 第 7 步查不到它们,
           # 于是"容器里跑的是旧守卫"这种失效只会以"守卫突然红了"的形式出现 (反过来, 旧守卫
           # 恰好绿的时候完全无声)。判据已进 test_dataset_door T9: 部署名单 ⊆ MD5_FILES。
           tools/test_std_supersede.py tools/test_std_guard.py tools/test_toxicity_cell.py
           tools/test_toxicity_match.py tools/test_unfreeze_cells.py tools/test_v01_staffing.py
           tools/test_ppe_vertical.py tools/test_must_cover_parity.py
           tools/test_export_payload_parity.py tools/test_number_provenance.py
           tools/test_health_table_header.py tools/test_front_matter.py
           tools/test_table_mounts.py tools/test_fresh_import.py
           # 2026-10-16 (S1c″): 标准号登记表从官方层表搬成**随代码走的 JSON** (用户拍板:
           # 官方层只装官方来源数据)。content_hold / load_exempt 读不到文件时是**具名抛错**,
           # 不静默当空集 —— 所以这两个文件必须进镜像, 否则导出链在容器里直接崩。
           # tools/test_std_existence.py 只在本机跑 (一手快照在 acquire/raw, 被 _EXCL 排除),
           # 但改动要送得进去、md5 要能比, 所以它也进清单; 它不在 GUARD_TESTS。
           std_render_hold.json std_code_exempt.json tools/test_std_existence.py
           # 存量正文清理工具: 生产要在容器里 exec 它 (dry-run → --apply), 所以它的字节
           # 也要有凭据 —— 不进 MD5_FILES 时第 7 步查不到它, 改了送不进去只会以"跑出来
           # 行为和文档不一样"的形式暴露。
           tools/fix_project_std_text.py tools/test_det_bucket.py
           # 2026-10-16 (#8 THI 口径统一到注6): 官方层存值的**重算工具** —— 生产那一行
           # (苯醌 轻度危害/下界29) 要在容器里跑 `--refresh-thi --apply` 才能跟上主口径,
           # 而它 COPY 是整目录 (`COPY tools/`), 不进 MD5_FILES 时第 7 步查不到它的字节,
           # "文档里的命令"和"容器里的那个脚本"就会各走各的。
           tools/backfill_hazard_toxicity.py
           # 2026-10-16 (#36 物质名下标位置): 一手 PDF 的下标感知取文本 + 迁移工具 + 判据。
           #   knowledge/pdf_cells.py 是"权威文本"的唯一产地 (工具与判据共用, 不各抄一份);
           #   test_oel_subscripts 需要 acquire/raw 里那份 PDF → 只进 LOCAL_TESTS, 不进 GUARD_TESTS
           #   (容器里没有它: .dockerignore 第 5 行 `raw`, 实测 git check-ignore 也一样)。
           knowledge/pdf_cells.py tools/fix_oel_subscripts.py tools/test_oel_subscripts.py
           # 2026-10-17 (#37 主子名粘连/名单元格截断): 一次性修复工具 + 结构判据。
           #   判据 R1/R2 读同一份一手 PDF (与 #36 同一个边界), 所以只进 LOCAL_TESTS;
           #   ⚠ 它同时是**数据耦合**的: R3 与"旧格值不得再现"两节断言的是**已修复的官方层**
           #   (1631 的 '人造矿物纤维绝热棉粉尘（玻' 之类)。生产那 4 行还没修 (进生产要用户单独
           #   批准) —— 批准之前把它塞进 GUARD_TESTS 就等于让部署在第 7 步红, 而红的是数据不是代码。
           #   批准并把迁移跑完之后再升进容器套, 那时它管的是"以后不许再长出这种名字形状"。
           tools/fix_oel_name_forms.py tools/test_oel_name_forms.py
           # 2026-10-17 (P1 #47 + #52): 两支**存量数据迁移**工具。生产要在容器里 exec 它们
           #   (dry-run → --apply), 字节没有凭据时第 7 步查不到"文档里的命令"和"容器里的脚本"
           #   是不是同一份 —— 上次 #35 就是靠这条发现 fix_project_std_text 的。
           #   · fix_cached_limit_table: 客户数据里过期的 built_tables['接触限值表'] 可重放重算
           #     (判据 A/B/C 与"换数"清单见文件头, --selftest 证明三条拒绝条件真能红);
           #   · add_samr_std_codes: 5 个真号补进 standard_db (SAMR 一手逐号核过, 幂等)。
           tools/fix_cached_limit_table.py tools/add_samr_std_codes.py
           # 2026-10-17 (第二窗口之后 #53/#54/#47): 又三支工具 + 一支自检。
           #   · retire_2025_batch: GBZ/T 229.1/2/3—2010 与 192.1/2—2007 五行按**一手 PDF 封面
           #     『代替…』逐字派生**判废止, 并把新行 replace_of 从 name 搬进字段 (默认 dry-run,
           #     幂等, 写后逐行读回 + 行数不变)。代替关系与生效日都从 PDF 现读, 不手写号与年份。
           #   · add_mohurd_std_codes: GB50009-2012 / GB50736-2012 登记 (住建部公告第1405/1270号
           #     一手页逐字读到); GB50021-2001 四条路径取不到一手 → 保持红, 不在本工具里。
           #     这两支生产都要 exec (dry-run → --apply), 所以进 MD5_FILES。
           #   · detect_cache_drift / test_detect_cache_drift: **只读**跨章缓存漂移探测器 (#47 第 1 步)
           #     章归属从 report_struct.CHAPTERS + word_export._SUB_TABLE_MAP 派生, 脱节即 rc=2;
           #     它不改数据、不进容器守卫套 (生产的 built_tables 存量实测为 0, 分母为空时它只能报"无项
           #     可查", 那不是判据) —— 只进 MD5_FILES 管字节凭据。
           tools/retire_2025_batch.py tools/add_mohurd_std_codes.py
           tools/detect_cache_drift.py tools/test_detect_cache_drift.py
           # 2026-10-17 (#53 存量正文): 已生成正文里"写着废止版号"的逐处判类器 (只读)。
           #   改写目标**反向从数据派生** (谁 state='废止' 且另一行 replace_of 指到它), 判据三维:
           #   级名配对(新版 PDF 正文解析) / 句中级别结论 / 旧书名耦合 (name 从目录取) → A/T/L/B/C。
           #   生产**不在容器里跑它**: 级名表要 acquire/raw 那五份一手 PDF, 镜像不含 raw,
           #   在容器里跑只会拿到"判据取不到 → 全落 C"的空绿 (§9.8 那类换环境换尺子的账)。
           #   执行方式与 §7.6 同一套路: 容器内 backup API 取一致性副本 → scp 本机 →
           #   OHS_DATA_DIR=<副本> 带 PDF 跑 --scan。它当前**拒绝写入** (ALLOW_WRITE=False),
           #   进 MD5_FILES 只为字节凭据 —— 清单里的命令与服务器上的脚本必须是同一份。
           tools/rewrite_stale_std_citations.py
           # 2026-10-17 (#44): 成品"标准引用形态"的只读枚举器 + 它的自检。
           #   它把"无年份 / 大小写 / 系列总称或占位 / 分隔符变体 / 词内断字"按
           #   "为什么现有尺子看不见"分桶, 并与两把真尺子 (test_std_existence 的 STD_COLLECT_PAT、
           #   std_guard 的 STD_REF_PAT) 做**判据级**交叉对表 —— 实测本机 68 份成品 + 56 行
           #   project.data: 号形 34,120 条, 网外 5,733 / 网内 28,387。
           #   不进容器守卫套 (它要读 data/report_*.docx, 镜像里没有成品), 只进 MD5_FILES 管字节凭据。
           tools/loose_std_refs.py tools/test_loose_std_refs.py
           # 2026-10-16 (P1 单元② #39 作业分级按 category 路由): grade_engine 是**改到的那张表**的
           # 产出点, 不在清单时第 7 步查不到它的字节 —— 容器里跑旧分级引擎、本地测试全绿
           # 就是"部署了但没生效"的静默假成功 (2026-09 knowledge/ 整目录排除那次同款)。
           knowledge/grade_engine.py tools/test_grade_routing.py
           # 2026-10-16 (P1 单元① #42/#43/#45): 代替关系/存在性的新判据 + 两支被 chain_audit、
           # db_writers、test_acceptance_readonly import 的度量模块。后四者以前只靠
           # `COPY tools/` 整目录进镜像, md5 一层没有凭据: 它们在容器里缺/旧时, 崩的是
           # 别的守卫, 报出来的是一条看不出根因的红。
           tools/test_std_supersede_consistency.py tools/tool_liveness.py tools/write_sites.py)

# 容器内守卫套 (第 4 步, 切流量之前跑): 必须选**与项目业务数据无关**的测试,
# 否则生产库一有差异就误判成"部署坏了"。选取口径见 tools/data_stamp.py 同款注释。
GUARD_TESTS=(test_std_ref_parity test_std_currentize test_std_supersede test_std_guard
             test_toxicity_cell test_toxicity_match test_unfreeze_cells test_v01_staffing
             test_ppe_vertical test_must_cover_parity test_export_payload_parity
             test_number_provenance test_health_table_header test_front_matter
             test_gate_sensitivity test_gate_coverage_selftest test_dataset_door
             test_door_currentize_unit test_import_purity
             # 2026-10-16 (P1 单元② #39): 作业分级按 hazard_factor.category 路由的守卫。
             # 进容器套之前实测过三种环境: 本地 120 绿; **删掉 project 表**的副本 117 绿 / 2 具名跳过
             # (生产库可能一个项目都没有, 那不是代码错 —— 2026-10-14 test_toxicity_cell 硬读本地行
             #  在容器里 TypeError 的同款教训, 所以 B3/B4 走具名跳过);
             # **删掉 hazard_factor** 的副本必须红 (rc=1, "类别取值域为空=下面全是空转" 那条探测),
             # 也就是说它不会因为生产数据少就假绿, 也不会因为生产没项目就假红。
             test_grade_routing)

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

# 只在本机跑的行为级守卫 (不进第 4 步的容器套): 它们要**真跑一次导出/一次 import 子进程**,
# 容器里跑会把生产挂载库当被测对象, 本地跑才是要保护的那条链。
#   test_import_purity      → import web.app 必须零副作用 + 生命周期回收的删除闸
#   test_acceptance_readonly→ 验收链跑完 data/ 必须零写入 (四把尺子 + 变异对照)
# ⚠ tools/local_deploy_drill.py **两个数组都不进**: 它是"起一台真 uvicorn --workers 2
#   并复制 6MB 库"的部署演练, 手工跑 (容器里跑等于自己跟自己抢 18099, 还会读宿主机 data/)。
#   它只进 MD5_FILES —— 交付完整性。改启动钩子/回收开关后请本地跑它一次。
# 本机守卫 (第 0 步, 开 SSH 之前先证代码): 需要 acquire/raw 的一手 PDF/快照, 容器里没有那些文件。
#   test_oel_subscripts (#36): 权威文本集合来自 GBZ_2.1_2019.pdf —— 被 .dockerignore 的 `raw` 挡住,
#   在容器里只能短路成一条"读不到来源"的红, 那既不是判据也不是部署坏了。
#   test_std_supersede_consistency (#42/#43/#45): 代替关系与存在性的双向一致性判据 ——
#   它要读 acquire/raw 的目录快照做"快照 status 全是'现行', 所以只能判存在不能判现行"那段统计,
#   容器里没有 raw (F 段自己会跳过, 但整支的定位是本机数据侧待办的点名工具, 不是容器回归)。
#   退出码只由判据+变异决定; 那张"该判废止而未判"的清单要 --strict 才红 —— 本批不灌字段不纠 state,
#   所以默认 rc=0 是设计, 不是"没测" (清单本身会打印条数, 复算见计划 §23)。
LOCAL_TESTS=(test_import_purity test_acceptance_readonly test_oel_subscripts
             test_std_supersede_consistency
             # 2026-10-17 (#37): 名字形状的结构判据。R1/R2 要一手 PDF (raw 不在镜像),
             # 且 R3/残词两节断言的是**已修复的本地官方层** —— 生产那 4 行还没迁移 (需单独批准),
             # 所以它现在是本机回归而不是容器门槛。理由与升格条件写在上面 MD5_FILES 那段。
             test_oel_name_forms)

echo "=== 0. 本地行为守卫 (先证代码, 再开连接) ==="
for _t in "${LOCAL_TESTS[@]}"; do
  printf '    %-28s' "$_t"
  if .venv/bin/python "tools/$_t.py" >"/tmp/ohs_${_t}.log" 2>&1; then
    echo "✓ $(grep -oE '[0-9]+ 绿 / [0-9]+ 红|通过 [0-9]+ / 失败 [0-9]+' "/tmp/ohs_${_t}.log" | tail -1)"
  else
    echo "✗ 见 /tmp/ohs_${_t}.log"; tail -20 "/tmp/ohs_${_t}.log"; exit 1
  fi
done

echo "=== 0.5 建立唯一一条复用连接 ==="
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
_SRC=(web tools acquire knowledge requirements.txt Dockerfile data_stamp.json quick_deploy.sh
      std_render_hold.json std_code_exempt.json)
# COPYFILE_DISABLE=1: macOS 的 tar 默认给带 xattr 的文件**再加一份 AppleDouble 侧车** `._xxx`
#   (远端解压时会跟着报一屏 `tar: Ignoring unknown extended header keyword
#    'LIBARCHIVE.xattr.com.apple.provenance'`)。2026-10-16 实测服务器上因此堆了 280+12 个
#   `._*`, 它们会被 `COPY web/` 一起打进镜像 —— 内容是二进制元数据, 不会被 import,
#   但它让"源码清单对表"(第 2.5 步)噪声盖过信号, 也让目录里多了几百个没人认领的文件。
COPYFILE_DISABLE=1 tar czf /tmp/ohs_src.tgz "${_EXCL[@]}" "${_KN_EXCL[@]}" "${_SRC[@]}"

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

echo "=== 2.5 服务器源码树僵尸核对 (建之前, 单条 ssh) ==="
# ⚠⚠ 教训 (2026-10-16 部署窗口): 第 2 步的 `tar xzf` 是**覆盖式解压, 从不删除** →
#   服务器源码树只增不减, 而 `COPY web/` 把整目录打进镜像。S1b′-1 把标准号模块
#   web/ → knowledge/ 搬走之后, 老的 `web/standard_version.py` 一直躺在服务器上
#   (17KB, Oct 15 00:22, 本地工作树与 git 里都没有), 每次构建都跟着进镜像。
#   这次是 test_dataset_door 把它抓出来的 —— 僵尸文件里带着"裸 sqlite3.connect +
#   data/ohs.db 字面量", T2 三条 + T5 一条当场红, 部署在第 4 步中止 (设计如此, 现网未切)。
#   但**内容干净的旧副本门是看不见的**: md5 第 7 步只校"清单里该有的文件字节对不对",
#   从不问"镜像里多了什么"。所以清单层自己对一次表 —— 方向是"服务器有、本地没有"。
#   (反方向"本地有、服务器没有"由第 7 步 md5 管, 两处合起来才是对称差。)
#   放在构建之前: 僵尸该删就删, 不该花 8 分钟构建出一个带退役源码的镜像再让守卫去发现。
_srvpy=$(mktemp); _lclpy=$(mktemp)
find web tools knowledge -name '*.py' -not -path '*__pycache__*' | LC_ALL=C sort > "$_lclpy"
if ! ssh_run "cd $REMOTE_DIR && find web tools knowledge -name '*.py' -not -path '*__pycache__*'" \
       | LC_ALL=C sort > "$_srvpy"; then
  echo "  ⚠ 远端清单取不到 (目录形状与预期不符?), 本步跳过核对但仍继续部署"
else
  _zom=$(LC_ALL=C comm -13 "$_lclpy" "$_srvpy")
  if [ -n "$_zom" ]; then
    echo "❌ 服务器源码树里有本地不存在的 .py (退役文件没删干净, 会被 COPY 打进镜像):"
    echo "$_zom" | sed 's/^/      /'
    echo "   处置: 确认是搬家/改名留下的旧副本后在服务器上删掉 (`ssh $SRV` → rm), 再重跑本脚本。"
    echo "   别把它当构建缓存留着 —— 它不会自己消失, 只会让下一次的红测试看起来莫名其妙。"
    exit 1
  fi
  echo "    僵尸源码 0 个 (两边各 $(wc -l < "$_lclpy" | tr -d ' ') 个 .py, 对称差为空)"
fi
rm -f "$_srvpy" "$_lclpy"

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
