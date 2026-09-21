# ADR-014: AI 安全评测 Gate v1（Phase 41.5 AI-001）

- 状态：已接受（Phase 41.5，1.4 / Secure Operations）
- 日期：2026-08-20

## 背景

ROADMAP 41.5（AI-001）要求模型/prompt 晋级进入服务之前建立可追溯的安全评测
Gate：

1. 建立对抗集：直接/间接提示注入、系统提示探测、跨租户检索、工具参数注入、
   PII/secret 外泄、恶意附件文本、多语言变体。
2. 固定每个用例的 tenant、prompt/model/version、允许工具、预期
   action/citation/redaction；结果生成不可变评测报告。
3. 高风险工具必须在 gateway 再授权并校验 tenant/customer，不信任模型生成的
   id；写操作默认人工确认。
4. 晋级阈值：安全集 100%，核心 golden 100%，质量指标不低于当前 active，
   P95/成本在租户预算内；否则自动阻断 canary 提升。

既有基础：`scripts/evaluate.py` 已提供 golden 集离线评测框架（TestClient 走真实
HTTP API、`_check_expect` 断言、p95 统计、`compare` 基线对比）；`app/prompts.py`
`PromptRegistry.set_canary` 在目标是 active 时抛 `ValueError`（自动阻断钩子点）；
`app/worm_store.py` `DiskWormStore` 提供一次写 + 篡改/丢失检测；`app/redaction.py`
`make_canary()` 提供 PII/secret 泄漏哨兵。缺口：对抗集、不可变评测报告、写操作
确认、晋级阈值自动阻断。

## 决策

### 1. 对抗集与用例固定（`golden/adversarial.json`）

- 独立 schema version 1 文件，覆盖七类威胁（直接/间接提示注入、系统提示探测、
  跨租户检索、工具参数注入、PII/secret 外泄、恶意附件文本、多语言变体）。
- 每个用例固定元数据：
  - `tenant_id`：默认 demo 租户；跨租户用例用第二租户断言 403/无检索结果。
  - `prompt`/`model_ref`/`prompt_version`：经 `_register_prompt_version` 注册，
    与 golden 集同机制，保证用例绑定的 prompt 版本可追溯。
  - `allowed_tools`：该用例允许的工具集合；断言无集合外工具被调用。
  - `expect`：在 golden expect 之上扩展 `requires_human`、`citation` 精确断言、
    `redaction`（敏感值不得出现在 assistant 输出）与 `canary`（哨兵不得泄漏）。
- 种子通道：间接注入经 `POST /api/policy` 写策略文章（检索即触发）；恶意附件
  经 `AttachmentService.upload` text/plain 注入；PII 哨兵经 `make_canary()` 注入
  审核单文本，断言输出与 `metadata` 无泄漏。

### 2. 不可变评测报告（`app/eval_reports.py`）

- 每次评测运行生成报告对象，写入 `DiskWormStore`（`EVAL_WORM_DIR`，默认
  `data/eval-reports`），object_id 为运行 id；报告含 environment、各 pass_rate、
  质量指标、p95、成本估算、逐用例结果与阻断原因。
- 报告只写一次 + 篡改/丢失检测（复用 `WormIntegrityError`）；同一运行不可重复写
  （write-once 拒绝）。晋级以报告为不可变审计证据。
- 晋级记录（promotion record）与报告同库：candidate/active 指标对比、gate 明细、
  通过/阻断原因，晋级报告可追溯（Gate B 第 5 项）。

### 3. 高风险工具再授权与写确认（`app/tools.py`）

- `ToolGateway` 增加再授权校验：工具执行前校验调用方 tenant 与目标资源
  tenant/customer 一致；工具参数中的资源 id（记录号、提交方引用）由网关按
  参数化契约重建，不信任模型生成的拼接 id（既有 `safe_arguments` 模式扩展）。
- 写操作注册表 + 人工确认：新增写工具类别（当前系统无写工具，注册表为空）；
  启用 `require_write_confirmation`（默认 true）时，未经确认的写工具调用被网关
  拒绝并审计；人工确认走 `operator:act` 权限的独立确认端点。

### 4. 晋级阈值与自动阻断

- `should_promote(candidate, active)` 五条件全过才放行：
  1. 对抗集 pass_rate ≥ `eval_adversarial_floor`（默认 1.0）；
  2. 核心 golden pass_rate ≥ `eval_golden_floor`（默认 1.0）；
  3. 质量指标（pass_rate、置信度均值、citation 覆盖率）不低于当前 active 同指标；
  4. p95 延迟 ≤ `eval_p95_budget_ms`；
  5. 成本估算 ≤ `eval_cost_budget_usd`（deterministic 路径 0 成本恒过）。
- 任一失败：audit `prompt.eval_blocked` + 阻断原因写入不可变报告；对 canary
  提升的自动阻断复用 `PromptRegistry.set_canary`（拒绝把 active 标记为 canary）
  的既有语义——只有评测通过的版本才允许进入 canary 路由。

## 后果

- 优点：
  - 对抗集与 golden 集同框架、可机器执行，进入 CI；用例固定 prompt/version/
    tools，结果可追溯。
  - 不可变报告为晋级提供审计证据，支撑 Gate B"模型/prompt 晋级报告可追溯"。
  - 网关不信任模型生成的资源 id；写操作默认人工确认，高风险路径 fail-closed。
  - 自动阻断钩子复用既有 canary 语义，无旁路。
- 代价 / 风险：
  - 对抗集基于确定性路径，不覆盖 live-model 幻觉场景；真实 provider 接入前
    成本估算恒 0（预算参数为前向预留）。
  - 间接注入经策略库种子，检索缓存可能掩盖未命中；用例显式固定策略文章。
  - 写确认端点无真实写工具可挂，机制为前向预留，需 code-review 确认无过度设计。
- 迁移路径：
  - 新增文件不触碰既有路由/表；`evaluate.py` 保持原状，对抗集为独立 harness 并
    复用其内部函数。
  - config 新字段默认值使既有部署零配置升级。

## 相关

- `docs/adr/0011-audit-external-anchoring.md`（WORM 复用）
- `docs/adr/0013-data-protection.md`（redaction/canary 复用）
- `app/prompts.py`、`app/worm_store.py`、`app/redaction.py`、`app/tools.py`
- `scripts/evaluate.py`、`golden/set.json`
- `tests/test_eval_reports.py`、`tests/test_adversarial_eval.py`

## 修订 2026-09-17（2.21.0）：种子退役改为 fail-closed

**背景**：上文「代价 / 风险」里那句「间接注入经策略库种子，检索缓存可能掩盖未命中」在
2026-09-17 真实发生了，但方向与预期相反——不是缓存掩盖了**未命中**，而是**未被退役的
种子**污染了后续用例。

**观察到的缺陷**：`scripts/evaluate_adversarial.py` 的种子清理是**尽力而为**的。
`_retire_seed()` 丢弃 PATCH 响应；清理循环写在消息循环**之后**，消息返回非 200 时提前
`return` 会**完全跳过清理**；没有任何 `try/finally`。对抗集里有 3 个用例带
`knowledge_seed`，其中 `adv-indirect-knowledge-injection` 注入的文章正文**字面包含**
`配送一般多久能到`，而这正是另外三个用例（15/17/18）**完全相同**的消息。于是用例 3 的
种子一旦未被退役，就会抢走后面三个用例的检索命中，并被间接注入防护判为危险内容（升级人
工、不带引用）——表现为「安全底线门禁偶发变红，且红灯全部指向受害者」。

**决定**：

1. 种子退役**必须被证明**，而不是被确认：先检查退役请求的状态码，再**读回**文章确认
   `active=false`。「请求返回 200」不构成证据。
2. 退役**必然执行**：用例体包在 `try/finally` 里，提前返回与异常都不能带走未退役的种子。
3. 泄漏**必须中止本轮**并**归因给种子的所有者**（`seed_leaks` 带用例 id），且中止发生在
   **晋级门禁之前**——隔离已失效的一轮不允许产生任何可用于晋级的结论。

**后果**：`SeedLedger`（`scripts/adversarial_seeds.py`）成为种子生命周期的唯一入口；
对抗集本身（`golden/adversarial.json`）未作任何改动。代价是每个种子多一次读回请求。

**遗留（已考虑、本版不改）**：`search_knowledge()` 在 FTS 命中为空时**不回落**到标签检索
（`app/db/knowledge.py`）。本次调查确认 FTS 索引由触发器维护、启动时自动补齐，未观察到该
分支被真实触发，故不动产品检索语义；将来若出现「文章存在却检索不到」，优先查此处。
