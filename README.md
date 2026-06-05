# 智能告警修复系统

**版本**: v2.12  
**日期**: 2026-03-26  
**作者**: OpenClaw

---

## 📋 系统概览

本系统用于自动检测和修复 DolphinScheduler 数据仓库中的数据质量告警，包含两大核心模块：

| 模块 | 功能 | 执行频率 |
|------|------|----------|
| **智能告警修复** | 扫描告警、修复数据、执行复验 | 每日 06:40 |
| **异常调度检测** | 检测并停止异常调度实例 | 每半小时 |

---

## 📁 目录结构

```
.
├── README.md                    # 本文件 - 项目入口
├── SYSTEM_MEMORY.md             # 系统记忆摘要 - 关键信息速查
│
├── config/                      # 配置模块
│   ├── config.py                # 配置管理
│   ├── auto_load_env.py         # 环境变量自动加载
│   └── README.md
│
├── core/                        # 核心定时任务 ⭐
│   ├── auto_stop_abnormal_schedule.py  # 异常调度检测
│   ├── repair_strict_7step.py          # 智能告警修复
│   ├── send_tv_report.py               # TV报告发送
│   └── README.md
│
├── modules/                     # 功能模块
│   ├── alert/                   # 告警处理模块
│   │   ├── alert_query_optimized.py
│   │   ├── send_alert.py
│   │   ├── db_config.py
│   │   └── README.md
│   │
│   └── dolphinscheduler/        # DS操作模块
│       ├── check_running.py
│       ├── search_table.py
│       ├── run_fuyan_workflows.py
│       ├── schedules_export.csv
│       └── README.md
│
├── tools/                       # 工具脚本
│   ├── extract_ds_sql.py        # DS内嵌SQL提取
│   ├── extract_ds_sh_usage.py   # DS中.sh资源使用清单
│   ├── fill_ds_workflow_resources.py   # DS任务资源文件回填
│   ├── update_ds_dwd_shell_script.py   # DWD脚本/环境批量更新
│   ├── task_execution_checker.py # 执行检查工具
│   └── README.md
│
├── docs/                        # 文档
│   ├── WORKFLOW_DOCUMENTATION.md       # 完整流程文档
│   ├── TASK_EXECUTION_WORKFLOW.md      # 执行流程详解
│   ├── EXECUTION_GUIDE.md              # 执行指南
│   ├── STOP_INSTANCE_API.md            # API文档
│   └── README.md
│
├── templates/                   # 配置文件模板
│   ├── AGENTS.md.template
│   ├── SOUL.md.template
│   ├── USER.md.template
│   ├── TOOLS.md.template
│   ├── IDENTITY.md.template
│   ├── BOOTSTRAP.md.template
│   └── README.md
│
├── data/                        # 数据文件
│   ├── cron_jobs/               # 定时任务记录
│   ├── auto_repair_records/     # 操作记录
│   └── README.md
│
└── memory/                      # 每日记忆记录
```

---

## 🚀 快速开始

### 环境准备

```bash
# 推荐先复制模板，再按国家/集群填写
cp .env.example .env.local

# 至少需要这些核心变量
export DS_BASE_URL='http://your-ds-host:12345/dolphinscheduler'
export DS_TOKEN='your_token'
export DS_PROJECT_CODE='your_main_project_code'
export DS_FUYAN_PROJECT_CODE='your_fuyan_project_code'
export DS_ENVIRONMENT_CODE='your_environment_code'
export DS_TENANT_CODE='your_tenant_code'
export DB_PASSWORD='your_password'
```

多国家迁移说明见 [docs/country-config-migration.md](/Users/jiangchuanchen/Desktop/PH-Intelligent-Alarm-Repair-Assistant/docs/country-config-migration.md)。

### 首次部署（复现指南）

```bash
# 1. 克隆代码
git clone git@github.com:chenjiuxuan1/PH-Intelligent-Alarm-Repair-Assistant.git
cd PH-Intelligent-Alarm-Repair-Assistant

# 2. 初始化配置（复制模板）
cp templates/AGENTS.md.template AGENTS.md
cp templates/SOUL.md.template SOUL.md
cp templates/USER.md.template USER.md
cp templates/TOOLS.md.template TOOLS.md
cp templates/IDENTITY.md.template IDENTITY.md
mkdir -p tmp

# 编辑这些文件，填写你的配置...

# 3. 配置环境变量
source ~/.bashrc

# 4. 验证配置
python3 tools/task_execution_checker.py --task all

# 5. 测试执行
python3 core/auto_stop_abnormal_schedule.py
python3 core/repair_strict_7step.py
```

### 手动执行

```bash
# 异常调度检测
python3 core/auto_stop_abnormal_schedule.py

# 智能告警修复
python3 core/repair_strict_7step.py
```

---

## 📝 核心脚本说明

### 1. 异常调度检测 (`core/auto_stop_abnormal_schedule.py`)

**8步执行流程：**
1. 加载调度配置
2. 获取运行中实例
3. 异常检测
4. 自动停止异常实例
5. TV通知（有条件）
6. 钉钉报告（已禁用）
7. 保存检测记录
8. 执行完成

**通知策略：** 只有发现异常时才发送TV通知，正常情况下静默

### 2. 智能告警修复 (`core/repair_strict_7step.py`)

**8步执行流程：**
1. 扫描告警（单次执行内去重）
2. 查找工作流位置
3. 执行修复（带限制检查）
4. 记录+复验（智能选择）
5. 钉钉报告（已禁用）
6. 保存操作记录
7. TV报告
8. 执行完成

**复验智能选择：**
- `dwb_`开头表 → 1级表复验
- 其他表 → 3级表复验
- 每日全级别必跑

---

## 🔧 常用工具

### 提取 SQL 代码

```bash
python3 tools/extract_ds_sql.py --project-name <project_name> --output ./sql_export/<project_name>

# 示例
python3 tools/extract_ds_sql.py --project-name DW_DM --output ./sql_export/DW_DM
```

### 检查任务环境

```bash
python3 tools/task_execution_checker.py --task all
```

### DS 调度 API 脚本

下面这 4 个脚本是当前仓库里已经按 `http://127.0.0.1:12345/dolphinscheduler` 打通、并在实际环境中验证过的 DS 调度 API 脚本：

| 脚本 | 用途 | 当前状态 |
|------|------|----------|
| `tools/extract_ds_sql.py` | 拉取项目工作流详情并导出内嵌 SQL | 已实测 |
| `tools/extract_ds_sh_usage.py` | 导出项目中 `.sh` 资源/脚本引用清单 | 单测通过 |
| `tools/fill_ds_workflow_resources.py` | 给指定工作流任务补 `resourceList` | 已实测 |
| `tools/update_ds_dwd_shell_script.py` | 批量更新 DWD 任务脚本和环境 | 已实测 |

通用环境变量：

```bash
export DS_BASE_URL='http://127.0.0.1:12345/dolphinscheduler'
export DS_TOKEN='your_token'
```

本地访问 DS 前，通常需要先建立 SSH 隧道：

```bash
ssh -N -f -L 12345:10.20.84.176:12345 -i /Users/jiangchuanchen/Desktop/id_rsa_pak jiangchuan@8.222.174.163 -p 36000 -o ServerAliveInterval=60 -o TCPKeepAlive=yes
```

快速检查隧道是否正常：

```bash
lsof -iTCP:12345 -sTCP:LISTEN -n -P
curl -s 'http://127.0.0.1:12345/dolphinscheduler/projects?pageNo=1&pageSize=1' -H "token: $DS_TOKEN"
```

如果端口被旧隧道占用，可以先清掉再重建：

```bash
pkill -f 'ssh -N -f -L 12345:10.20.84.176:12345'
```

常见用法：

```bash
# 1. 导出指定项目的内嵌 SQL
python3 tools/extract_ds_sql.py --project-name DW_DM --output ./sql_export/DW_DM

# 2. 导出多个项目中的 .sh 使用清单
python3 tools/extract_ds_sh_usage.py DW_DM DW_DWD DW_RPT --output ./sql_export/all_projects_sh_usage.csv

# 3. 给 DWD 工作流批量补资源文件（先 dry-run，再 apply）
python3 tools/fill_ds_workflow_resources.py --project-name '巴基斯坦-数仓工作流_new' --workflow-name 'DWD'
python3 tools/fill_ds_workflow_resources.py --project-name '巴基斯坦-数仓工作流_new' --workflow-name 'DWD' --apply

# 3.1 对已有 resourceList 做统一回刷
# DWD_SEC / DWD_SEC（1D）这类按固定资源根目录重建的场景
python3 tools/fill_ds_workflow_resources.py \
  --project-name '巴基斯坦-数仓工作流_new' \
  --workflow-name 'DWD_SEC' \
  --resource-root 'deploy/resources/starrocks_workflow/dwd_sec' \
  --overwrite-existing --apply

# 3.2 所有命中任务统一使用同一个资源文件
# 适合 kbm_summary_reprint_12h 这类所有任务共用一个 SQL 资源的工作流
python3 tools/fill_ds_workflow_resources.py \
  --project-name '巴基斯坦-数仓工作流_new' \
  --workflow-name 'kbm_summary_reprint_12h' \
  --resource-root 'deploy/resources/starrocks_workflow/kbm/kbm_summary' \
  --resource-name-override 'dolphinscheduler/resource/deploy/resources/starrocks_workflow/kbm/kbm_summary/kbm_summary.sql' \
  --overwrite-existing --apply

# pak_sr 这类 bash pak_sr/... 任务，保留原相对路径，只转换成 DS fullName
python3 tools/fill_ds_workflow_resources.py \
  --project-name '巴基斯坦-贷后数仓_new' \
  --workflow-name 'DWD明细层构建(1)_import_20260402150146514' \
  --overwrite-existing \
  --reuse-existing-relative-paths \
  --resource-prefix 'dolphinscheduler/resource/dolphinscheduler/resources' \
  --apply

# 4. 批量替换 DWD 任务脚本并切换环境到 dw_platform
python3 tools/update_ds_dwd_shell_script.py --project-name '巴基斯坦-数仓工作流_new' --workflow-name 'DWD'
python3 tools/update_ds_dwd_shell_script.py --project-name '巴基斯坦-数仓工作流_new' --workflow-name 'DWD' --apply

# 4.1 强制统一替换所有 SHELL 任务脚本
python3 tools/update_ds_dwd_shell_script.py \
  --project-name '巴基斯坦-数仓工作流_new' \
  --workflow-name 'DWD_SEC' \
  --replace-all-shell-scripts \
  --apply
```

说明：
- `tools/` 目录下这 4 个脚本是当前维护的 DS API 主链路。
- `dolphinscheduler/` 目录里保留了一批历史脚本，部分仍使用旧路由或固定环境配置，适合参考，不建议直接当作现行脚本复用。
- `fill_ds_workflow_resources.py` 这次新增了 3 个关键能力：
  - `--overwrite-existing`：允许覆盖已有 `resourceList`
  - `--reuse-existing-relative-paths`：保留任务里的相对路径，只规范化前缀
  - `--resource-prefix`：把相对路径或展示路径转换成 DS 后端真正使用的 fullName
- 这次排查里踩到的关键陷阱：
  - UI 搜索到的资源路径，不一定等于 Worker 下载 OSS 用的 key
  - `pak_sr` 资源里，展示路径和资源中心 `fullName` 是两套不同字符串
  - 如果直接把展示路径写回 `resourceList`，前端可能能搜到，但执行时仍会 `NoSuchKey`
  - 脚本现在已经会自动规范 3 种输入：相对路径、展示路径、双前缀错误值
- `update_ds_dwd_shell_script.py` 新增了 `--replace-all-shell-scripts`，适合旧脚本文本不完全统一时批量替换。

---

## 📚 详细文档

| 文档 | 用途 | 位置 |
|------|------|------|
| **SYSTEM_MEMORY.md** | 系统记忆摘要 - 关键信息速查 | 根目录 |
| **WORKFLOW_DOCUMENTATION.md** | 完整系统流程文档 | docs/ |
| **TASK_EXECUTION_WORKFLOW.md** | 定时任务执行流程详解 | docs/ |
| **EXECUTION_GUIDE.md** | 执行指南 | docs/ |
| **STOP_INSTANCE_API.md** | 停止实例API文档 | docs/ |

---

## 🔑 关键配置

### 环境变量

```bash
DS_TOKEN              # DolphinScheduler Token
DB_PASSWORD           # 数据库密码
DB_HOST=172.20.0.235  # 数据库主机
DB_PORT=13306         # 数据库端口
DB_USER=e_ds          # 数据库用户
DB_NAME=wattrel       # 数据库名称
```

### 项目代码

| 项目名称 | Code |
|---------|------|
| 国内数仓-工作流 | 158514956085248 |
| 国内数仓-质量校验 | 158515019231232 |
| okr_ads | 159737550740160 |

---

## ⏰ 定时任务

| 任务 | 频率 | 超时 |
|------|------|------|
| 异常调度检测 | 每半小时（00,30分） | 300秒 |
| 智能告警修复 | 每日 06:40 | 1800秒 |

---

## 📞 联系信息

- **GitHub**: https://github.com/chenjiuxuan1/PH-Intelligent-Alarm-Repair-Assistant
- **用途**: 数据仓库监控和告警自动修复

---

**更多信息请查看 [docs/](./docs/) 目录下的文档。**
