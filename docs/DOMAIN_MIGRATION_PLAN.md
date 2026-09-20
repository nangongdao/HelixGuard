# 业务域迁移方案：智能客服 → AI 内容安全审核平台

> 状态：**待术语契约确认**（P1–P5 均未执行）
> 起因：本仓库当前定位为"多 Agent 智能客服平台"，该定位在简历场景中同质化严重，需要把业务外壳整体迁移到区分度更高的领域，同时保留全部平台能力。
> 目标业务域：**AI 内容安全审核平台**（提交内容 → 机审 → 人工复核 → 申诉/复审）

---

## 1. 现状取证（量化）

| 维度 | 规模 | 主要落点 |
| --- | ---: | --- |
| `conversation` | 2,919 处 / 97 文件（app）+ 2,653 处 / 133 文件（tests） | `app/db/`、`app/orchestrator.py`、全量测试 |
| `customer` | 671 / 67 + 719 / 102 | `app/db/core_schema.py`、`app/intake.py` |
| `operator` | 246 / 47 + 446 / 69 | `app/security.py`（Role）、`app/collaboration` |
| `ticket` | 255 / 10 + 184 / 5 | `app/db/tickets.py`、`app/routers/tickets.py` |
| `csat` | 107 / 16 + 95 / 12 | `app/db/`、`app/routers/csat.py`、`v14`/`v25` |
| `widget` | 169 / 15 + 349 / 17 | `app/widget_routes.py`、`app/widget_token.py` |
| 数据库 | 63 张表（约 20 张强客服语义）；48 个迁移版本（v01–v48） | `app/db/core_schema.py`、`app/migrations/` |
| API 契约 | 133 条路径，35–40 条含客服词段；154 个路由装饰器 | `api/openapi.json` |
| 前端中文文案 | 326 处 | `app/static/js/i18n.js`（单点）、`frontend/src/islands/` |
| 测试耦合 | 1,963 个 test 函数中 312 个命中；约 40 个测试文件名 | `tests/` |
| 视觉资产 | 7 张截图中 5 张带客服语义 | `docs/assets/screenshots/` |

**判断：业务外壳（客服）与平台内核（编排 / 模型治理 / 审计链 / 多租户）边界清晰，迁移不需要触碰内核。**

---

## 2. 术语契约（**执行前必须冻结**）

这是后续所有机械替换的唯一输入。一旦冻结，P2–P5 全部按此表执行；改一处需重跑下游。

### 2.1 产品名

| 候选 | 说明 |
| --- | --- |
| **Helix Guard**（推荐） | 保留 Helix 品牌连续性，Guard 直接表达"审核 / 守卫" |
| Helix Sentinel | 哨兵，偏安全运营 |
| Helix Review | 直白但平淡 |

### 2.2 术语映射

| # | 概念 | 旧标识符 | 新标识符 | 中文旧 → 新 | 规模 |
| --- | --- | --- | --- | --- | ---: |
| T1 | 提交方（外部） | `customer` | `submitter` | 客户 → 提交方 | ~1,390 |
| T2 | 提交方标识 | `customer_ref` | `submitter_ref` | 客户标识 → 提交方标识 | DB 列 |
| T3 | 审核员（人工） | `operator` | `reviewer` | 坐席 → 审核员 | ~692 |
| T4 | 指派审核员 | `assigned_agent` | `assigned_reviewer` | — | ~101 |
| T5 | 审核单 | `conversation` | `review_case` | 会话 → 审核单 | **~5,572** |
| T6 | 申诉单 | `ticket` | `appeal` | 工单 → 申诉单 | ~618 |
| T7 | 抽检评分 | `csat` | `qa_spot_check` | 满意度 → 抽检评分 | ~241 |
| T8 | 提交端 | `widget` | `submission_portal` | 网页挂件 → 提交端 | ~290 |
| T9 | 策略文章 | `knowledge_articles` | `policy_articles` | 知识库 → 策略库 | 表 |
| T10 | 风险类别 | `intent` | `risk_category` | 意图 → 风险类别 | 列 |
| T11 | 判定结论 | `resolution` / `resolved` | `verdict` / `decided` | 解决 → 判定 | 列 + 状态机 |
| T12 | 预置结论 | `canned_responses` | `canned_verdicts` | 快捷回复 → 预置结论 | 表 |
| T13 | 审核组 | `agent_groups` | `reviewer_groups` | 坐席组 → 审核组 | 表 |
| T14 | 来源溯源 | `order` | `source_lookup` | 订单 → 来源查询 | 模块 |
| T15 | 转人工 | `handoff` | **保留** | 转人工 → 转人工复核 | 语义已中立 |
| T16 | 升级 | `escalation` | **保留** | 升级 → 升级复审 | 语义已中立 |
| T17 | 队列 / 渠道 / 租户 | `queue` / `channel` / `tenant` | **保留** | — | 通用词 |

### 2.3 唯一保留的分歧点：T5

**T5（`conversation` → `review_case`）占全部改动量的约 70%**（5,572 处），但它的增量简历价值最低——`conversation` / `messages` 是通用软件词汇，读起来更像"通用消息系统"而非"客服"。

**建议：T5 单独作为最后一个阶段执行**，且在 T1–T4、T6–T14 全部落地、门禁稳定之后再动。如果中途想止损，T1–T4 + T6–T13 已经足以让仓库不再"一眼是客服"。

---

## 3. 关键约束：迁移门禁的 expand/contract 纪律

`scripts/migration_gate.py` → `check_expand_additivity()` 会把**整条迁移链执行在内存 SQLite 上**，对每个 `expand` 迁移做前后 schema 超集比对。实测（SQLite 3.53.1，探针见 §6）：

| 迁移标注 | 判定 |
| --- | --- |
| `expand` + `RENAME TABLE` | **REJECTED** — `dropped table conversations — not additive for N/N+1 coexistence` |
| `expand` + `RENAME COLUMN` | **REJECTED** — `removed column customer_name` |
| `contract` + `RENAME` | ACCEPTED（超集检查只作用于 `expand`） |
| 无标注（grandfathered）`RENAME` | ACCEPTED |

### 为什么不能"只加一个 RENAME 迁移"

`app/bootstrap.py:123` → `app/db/core_schema.py:538`：`initialize()` 与迁移链跑在**同一连接**里，且 `initialize()` 每次启动都以 `CREATE TABLE IF NOT EXISTS conversations` 形式重建旧表名。因此：

- 若基线改新名、迁移链仍建旧名 → v49 RENAME 撞上已存在的新表 → 失败
- 若基线不改、迁移链不改 → 每次启动重建空 `conversations`，与新表并存 → 脏数据

### 选定策略：**一致改写（consistently rewrite）**

1. `app/db/core_schema.py` 基线 + `v01`–`v48` 中的旧标识符**一致替换为新标识符**，不新增迁移。
2. 链条仍为 48 条连续；`check_expand_additivity` 仍通过——因为**整体一致重命名后，每一步相对前一步依然是超集**。
3. `_detect_legacy_version()` 的硬编码标记表 `"conversations"`（`app/migrations/__init__.py:138`）同步改为新名。
4. 含客服语义的迁移文件同步改名（`verify_migration_registry` 只校验 `v{N:02d}_*` 前缀，后缀可变）。

**代价（必须如实记录）**：迁移历史被改写，**已部署实例无法平滑升级，需重建库**。本项目无生产部署，可接受；CHANGELOG 需显式声明为破坏性变更。

---

## 4. 阶段划分（每阶段一个 PR，门禁全绿）

| 阶段 | 范围 | 门禁影响 | 风险 |
| --- | --- | --- | --- |
| **P1 叙事层** | 产品名、`pyproject.toml`、`package.json`、`README.md`、`CHANGELOG.md`、docs 标题、新增 `docs/DOMAIN.md` 术语表 | 无（不动代码） | 极低 |
| **P2 文案层** | `app/static/js/i18n.js`（326 处单点）、`frontend/src/islands/*`、`app/static/*.html`；重捕获 5 张截图 | `visual_gate`、`frontend_gate` | 低 |
| ↳ *实际落地* | 743 处替换 + 4 处回修跨 283 文件；**重捕获 7/7 截图**（非 5 张）；**视觉基线需一并重锚**（文案变更本身即像素变更，见 §6）；交付后审计追加**第二遍补漏**（提交端入口 + 审核员面残留词，`docs/DOMAIN.md` §3.1） | 同上，全绿 | 低（但暴露 1 处叠字缺陷，已修） |
| **P2b 内容夹具层** | 种子策略库正文、预置结论正文、提交端演示对话、来源查询工具状态串（`运输中`/`已出库`）、`golden/{set,adversarial}.json` | `evaluate.py --min-pass-rate 1.0`、对抗集 23/23、`golden` 门禁 | 中（分类器关键词与评测断言耦合，只改文案会三方不一致） |
| **P3 模块与路由** | `app/csat.py`→`qa_review`、`app/routers/{tickets,conversations,csat}.py` 改名、`app/widget_*.py`→`portal_*`；API 路径改名；重生成 `api/openapi.json`；同步 `clients/python/` | `openapi_snapshot`、`frontend_gate`、SDK 一致性测试 | **中**（见 R1） |
| **P4 数据层** | §3 的一致改写：`core_schema.py` + v01–v48 + 迁移文件名 + 8 个领域 mixin | `migration_gate`、`verify_migration_registry`、PG/SQLite 等价性 | **高** |
| **P5 测试与基线** | 312 个测试函数、约 40 个测试文件名重命名；性能预算复核；清理 `*.bak`；移除 README 迁移横幅 | 全量 1,963 测试、`performance_gate`、`threat_model_gate`、SBOM | **高**（见 R4）。视觉基线已在 P2 重锚，P5 只在 UI 再变时复锚 |

执行顺序：P1 → P2 → P2b → P3 → P4 → P5。**P4 与 P5 必须连续在同一批次完成**（改名后测试必红，不能分两次提交）。

---

## 5. 风险登记

| ID | 风险 | 影响 | 处置 |
| --- | --- | --- | --- |
| R1 | API 路径改名违反 `docs/API_POLICY.md` 的既有契约（"响应体只增不改、弃用需 `Deprecation`/`Sunset` 头 + 至少一个次版本过渡"） | 门禁/契约不一致 | 二选一：走完整弃用过渡（旧路径保留一个次版本）或显式在 ADR 中豁免并记录理由 |
| R2 | 迁移历史改写导致已部署库 schema 不兼容 | 无法平滑升级 | 无生产部署，可接受；CHANGELOG 声明破坏性变更 + 提供重建指引 |
| R3 | T5 的 5,572 处机械替换，遗漏一处即测试红 | 返工 | 先替换 + 后全量门禁；用 `\bconversation\b` 词边界而非裸串替换，人工复核 diff |
| R4 | 视觉基线 / 性能预算需重新锚定，可能触发 `MAX_STATIC_HEADROOM`（预算不得高于载荷 25%）断言 | 门禁红 | 按既有口径重锚（LF 归一化 tracked 载荷），不改断言 |
| R5 | 截图重捕获依赖 clean-DB 本地服务 + Playwright | P2 无法验收 | 复用 `scripts/readme_screenshots.py` 既有流程 |
| R6 | `app/database.py.bak`、`app/main.py.bak` 为遗留文件 | 仓库噪声 | P5 顺带清理（需确认无人依赖） |

---

## 6. 证据

- 迁移门禁基线：`python scripts/migration_gate.py --json` → `{"ok": true, "migrations": 48, "phased": 16, "problems": []}`
- RENAME 行为探针：`artifacts/probe_migration_rename_phase.py`（`artifacts/` 已 gitignore，与既有 `probe_widget_note_leak.py` 同惯例）。复跑：
  `python artifacts/probe_migration_rename_phase.py`。构造含 `RENAME TABLE` / `RENAME COLUMN` 的最小迁移链，分别以 `expand` / `contract` / 无标注提交，调用 `check_expand_additivity` 与 `check_migration_phases` 取判定（结果见 §3 表）。
- 启动路径：`app/bootstrap.py:123` → `app/db/core_schema.py:536-538`（同一连接内 `initialize()` 后跑 `run_migrations`）。

---

## 7. 下一步

1. 确认或修订 §2 术语契约（含产品名）。
2. 确认 T5 是否按建议拆为独立末阶段。
3. 契约冻结后从 P1 开始执行。
