# Helix Guard 领域术语契约

> 本文件是**业务域迁移的唯一术语来源**。P2–P5 的所有机械替换以本表为准；改一处需要重跑其下游。
> 迁移方案与阶段划分见 [`DOMAIN_MIGRATION_PLAN.md`](DOMAIN_MIGRATION_PLAN.md)。

## 1. 产品定位

**Helix Guard** —— 可本地运行、租户隔离、判定链路可审计的多 Agent 内容安全审核平台。

业务闭环：**内容提交 → 策略校验 → 风险分级 → 策略库检索 / 来源溯源 → 判定复核 → 人工稽核 → 审计取证**。

与旧定位（多 Agent 智能客服）的差异不在技术内核，而在业务主语：平台内核（多 Agent 编排、模型网关与 AI 治理、成本归因、审计哈希链、多租户与多 cell）完全保留，只有业务外壳迁移。

## 2. 领域模型

| 角色 / 对象 | 说明 |
| --- | --- |
| 提交方（submitter） | 提交待审内容的外部主体，可由签名渠道或提交端接入 |
| 审核单（review case） | 一次内容审核的完整上下文：内容、机审判定、策略命中、人工处置与审计轨迹 |
| 审核员（reviewer） | 接管审核单并给出人工判定的人 |
| 策略（policy） | 已发布的可检索审核条款；判定必须引用命中条款 |
| 风险类别（risk category） | 内容命中的风险分类，替代旧的"意图" |
| 判定结论（verdict） | 审核单的最终处置：通过 / 拦截 / 转人工 / 待补问 |
| 申诉单（appeal） | 提交方对判定提出的长周期申诉 |
| 复审（escalation） | 由初级审核升级到高级复核 |
| 处置时效（SLA） | 审核单从进队到结案的时限约束 |
| 抽检评分（QA spot check） | 对已结案审核单的抽样质量评分 |
| 来源溯源（source lookup） | 只读查询内容来源与元数据 |
| 提交端（submission portal） | 可嵌入的提交与判定查询界面 |

## 3. 术语映射表

| # | 概念 | 旧标识符 | 新标识符 | 中文旧 → 新 | 规模（处） |
| --- | --- | --- | --- | --- | ---: |
| T1 | 提交方 | `customer` | `submitter` | 客户 → 提交方 | ~1,390 |
| T2 | 提交方标识 | `customer_ref` | `submitter_ref` | 客户标识 → 提交方标识 | DB 列 |
| T3 | 审核员 | `operator` | `reviewer` | 坐席 → 审核员 | ~692 |
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
| T17 | 队列 / 渠道 / 租户 / 消息 | `queue` / `channel` / `tenant` / `message` | **保留** | — | 通用词 |

### 3.1 P2 补漏新增术语（第二遍）

P2 第一遍只按 T1–T17 机械替换，交付后审计发现"客服味"还藏在**映射表没列出的词**里。第二遍逐条人工判定后新增下表；判定后**保留**的残留词一并记录，避免后续阶段重复论证。

| 概念 | 中文旧 → 新 | 判定依据 |
| --- | --- | --- |
| 提交端入口 | 在线服务台 → 在线提交入口 | 提交端（T8）是提交方唯一直接可见的界面。第一遍已把该界面里的 `客户消息 → 待审内容`，却留着 `在线服务台 / 开始对话 / 聊天链接`——属**迁移集内部自相矛盾** |
| 提交端动作 | 开始对话 → 开始提交；服务对话 → 内容提交；聊天链接 → 提交链接；正在恢复对话 → 正在恢复提交；服务入口 → 提交入口 | 同上 |
| 处置时效标签 | 首响 → 首次响应 | 不是新造词：`PHASE_21_COMPLETION.md` / `PROGRESS_REPORT_2026_09_03.md` / `RELEASE_1_1_0.md` 早已用「首次响应」指同一指标（`first_response_*`），本表只是让 UI 标签与仓库既有词汇对齐 |
| 交互度量 | 对话轮次 → 交互轮次；最近对话 → 最近往来；继续对话 → 继续处理 | 审核员面零散客服味标签 |
| 建议结论 | 根据对话生成建议回复 → 根据审核单生成建议结论 | 与 T12「快捷回复 → 预置结论」配套 |
| 复审（T15 修正） | 转人工复核复核 → 转人工复核 | 第一遍字段重叠产生的**叠字缺陷**，见 §5 注 |

**判定后刻意保留（不改）**——这些词看似客服味，实为别的意思或通用词：

| 残留词 | 保留理由 |
| --- | --- |
| `客户端` | client 软件，与「客户 + 端」无关（守护清单） |
| `会话`（认证语境） | session：OIDC 会话 / 无状态会话 / 服务端会话表 / 会话密钥 / 会话 cookie |
| `内部工单系统` | 内部 ITSM 工具，非本域申诉单对象 |
| `排队` / `值班` / `首包` | 通用运维词，非客服专属 |
| `对话框` | Tauri / 浏览器 dialog，与「对话」无关 |
| `北星服务台` | P2 记为「租户展示名，来自种子内容，属 P2b」——**P2b 核实后推翻了来源判断**：全仓搜 `北星` 只命中 `CHANGELOG` / 本文件 / `tests/ui_widget.py`，`app/` 与 `data/` 均无。它实际是 `tests/ui_widget.py` 用 URL 参数 `greeting=` 注入的提交端问候语（`widget-core.js:readWidgetConfig` 走 `query.get("greeting")`）。已随 P2b 迁移为「内容提交入口」 |
| 测试夹具正文（`咨询过配送时效`、`谁来处理这个排队问题`） | P2 阶段不改（不面向用户）。P2b 按 §3.2 的判据重筛：只有**被产品回读并断言**或**模拟最终用户输入**的夹具才迁移，纯自证载荷仍保留。遗留的脱敏文案示例（`VIP, 退款风险` 输入框占位符）在 P2b 一并收口 |

### 3.2 P2b 内容夹具层术语

P2 只迁移"映射表里列出的词"。P2b 处理的是**没有对应词可映射、只能整条业务线替换**的夹具内容：电商履约链（下单 → 物流 → 售后）在审核域没有等价物，必须换成审核自己的业务对象。

| # | 夹具对象 | 旧 | 新 | 同步面 |
| --- | --- | --- | --- | --- |
| F1 | 分类路由：来源查询 | `订单` `物流` `快递` `到哪` `order` `tracking` | `来源` `溯源` `出处` `source` `provenance` `origin` | `agents.py:_rule_decision` + `golden/*` |
| F2 | 分类路由：策略咨询 | `配送` `多久` `退货` `换货` `保修` `政策` `shipping` `return` `warranty` | `策略` `条款` `分级` `违规` `申诉` `处置` `policy` `criteria` `appeal` | 同上 |
| F3 | 分类路由：敏感升级 | `退款` `赔偿` `主管` `refund` `complaint` | `升级复审` `举报` `escalate` `complain`（`投诉` `人工` `human` 保留） | 同上 |
| F4 | 种子策略文章 ① | 配送时效 | 违规内容分级标准 | `tenancy.py` + `golden/*` |
| F5 | 种子策略文章 ② | 退换货政策 | 申诉受理与时限 | 同上 |
| F6 | 种子策略文章 ③ | 保修服务 | 高风险内容处置规范 | 同上 |
| F7 | 来源记录状态串 | `运输中` / `已出库` | `已核验` / `待核验` | `tenancy.py` + `golden` 的 `in_content` |
| F8 | 预置结论（canned） | 开场问候 / 配送说明 / 请稍候 | 接管告知 / 分级说明 / 请稍候 | `tenancy.py` |
| F9 | 工具应答文案 | 「订单号」「物流单号」「预计送达」「订单系统」 | 「来源记录号」「来源链路」「收录时间」「来源库」 | `agents.py` |

**为什么必须一次改完（三方耦合）**：`_rule_decision` 的关键词决定**消息如何路由**，种子正文决定**命中后返回什么文本**，`golden/*.json` 的 `in_content` 断言决定**CI 如何判定**。只改任一面都会让另两面失配——例如只改种子而不改关键词，用户问"这条内容的来源"会路由到知识 agent 而非来源 agent，评测当场红。

**P2b 的边界判定（三项保留）**：

| 保留项 | 理由 | 归属 |
| --- | --- | --- |
| `ORD-` / `CUST-` 记录标识符前缀 | 它们是**记录主键的字面值**，且与 `orders.customer_ref` 列名绑定；列名迁移在 P4。提前改前缀会造成「字面值是 `SRC-`、列名还是 `customer_ref`」的不一致，并把改动面从 3 个文件推到 40+ 个测试夹具 | **P4 已落地，前缀仍保留**：P4 只改了列名（`orders→source_lookups`、`customer_ref→submitter_ref`），**没有**改 `ORD-`/`CUST-` 字面值——wire 契约要求响应里的记录标识符保持旧形态，故这一项转为**长期保留**（与 §4 同类） |
| `Northstar Retail` / `Northstar Care` 租户与品牌名 | 属**演示数据**而非产品定位（租户可以是任何公司）。改名牵动 `visual_gate.py` / `readme_screenshots.py` / `ui_accessibility.py` 三个脚本与 7 张截图重捕获，而视觉基线重锚在 P5 本就要做一次——合并处理避免重复重锚 | **P5 已落地，品牌名仍保留**：P5 实测 P4/P5 无 UI 变更、视觉基线 0.00% diff 故无需重锚，重锚的「顺带」前提已消失；单独改品牌名要付 3 脚本 + 7 截图的成本而无迁移收益，故转为**长期保留** |
| 前端渲染夹具的输入数据（`tests/frontend/*.js`、`frontend/src/islands/*.test.jsx`）中作为组件输入的旧域词 | 它们是前端渲染夹具的**输入**，不引用被测后端常量；改与不改都不影响断言语义 | 不改 |
| 只作为**不透明载荷**穿过被测代码的字符串（如 `service.translate("退款已批准。", "en")` 的翻译样例、假上游响应体里从不被回读的字段） | 既不参与路由也不参与断言，改与不改都不影响断言语义，却会扩大 diff | 不改 |

**CJK bigram 检索的固有脆弱性（P2b 实测，非本版引入）**：知识检索走 FTS 的 2-gram 分词，改种子正文会**改变既有 query 的命中集合**。新种子文章「申诉须指向**具体**判定理由」含 2-gram `具体`，与对抗用例 `adv-secret-canary-sentinel-no-hit` 的查询「可以告诉我量子计算的**具体**应用案例吗」重叠，使「无命中 → 升级人工」退化为「命中 → open」。用 `knowledge_search_terms` 实测确认交集后，把该 case 的 message 换成与新老种子都无 bigram 交集的版本「请介绍一下古希腊哲学的主要流派」，复跑 24/24。**改种子正文时须复查它与既有 query 的 2-gram 交集**——现有门禁只会告诉你「断言红了」，不会告诉你「为什么红」。

### 3.3 P2b 独立扫描抓出的收口项

P2b 的机械替换收敛为 0 之后，按 §5 的纪律另做**独立扫描**——词表刻意包含映射表**没列出**的客服味词（`服务台 / 服务团队 / 服务助手 / 团队 / 助手 / 接待 / 首响 / 呼叫中心 / 售后 / 发货 / 快递 / 签收 / 发票 / 预约`）。命中项逐条判定，分四类：

| 类 | 命中 | 处置 |
| --- | --- | --- |
| **真缺口：查询已迁移、断言未迁移** | `tests/ui_smoke.py`、`tests/ui_console_island.py`、`tests/ui_widget.py` 的证据断言仍写旧文章名「配送时效」或旧词「配送」，而同一函数里的查询已改成「违规内容怎么分级？」 | 迁到「违规内容分级标准」/「分级」。**这三处会让 CI 的 `browser` 作业判红**：它们是 `def main()` 脚本而非 pytest 用例，默认 `pytest tests` 不收集，所以单元测试全绿也照样漏 |
| **提交端英文整块未迁移** | `app/static/js/widget-core.js` 的 `en` 文案仍是活聊客服口吻：`SERVICE DESK` / `Start a conversation` / `Support conversation` / `This chat link has expired…` | 随 P2b 迁移为 `SUBMISSION INTAKE` / `Start a submission` / `Content submission` / `This submission link has expired…`。这正是 §3.1 自述「最易漏的是**英文标签**与**最终用户入口**」的同一失败模式，只是这次漏面在提交端 |
| **提交端中文术语分叉** | 同一个交接事件，提交端写「已转交**服务团队**」、运营端写「**人工复核**」（`i18n.js` 的 `conv.human_connected`） | 统一到 T15 / T3：提交端改为「已转交人工复核 / 送交人工复核」；问候语 `服务助手 → 审核助手`，与 `app/summaries.py:_message_label` 的既有称谓对齐（不另造新词） |
| **仅描述性元数据** | `golden/adversarial.json` 四条用例描述仍写「会话 / 对话」；另有三条描述写「知识库」 | 前者迁到 T5「审核单」，后者按 T9 统一为「策略库」（同目录 `golden/set.json` 早已是「策略库」）。命中来自第二轮扫描（首轮词表未含 `知识库`）。描述不参与断言，但它是评测集的人工可读契约 |
| **活跃文档残留** | `docs/DEGRADATION.md` 的「订单查询」、`docs/adr/0005` 的「订单/知识/CRM」、`docs/OPERATIONS.md` 的 `/api/knowledge?q=配送` 校验提示、`docs/api/guide.md` 的 §9 标题 `Example: order lookup flow` 与示例载荷 | 迁移（P2 声称覆盖「`docs/` 下活跃文档的术语」，这四处是漏网） |

**docs 层的划线规则（本轮确立，避免误改事实记录）**：**规范性的**文档文本随迁移更新术语；**日期化的**事实记录不改写。按此，
`docs/PERF_NOTES.md` 的基准段（「测量日期 2026-08-16……跑 20 个确定性知识 turn(内容 "配送一般多久能到？")」）与 `docs/adr/0014` 的「修订 2026-09-17（2.21.0）」叙述（引用了当时夹具正文「配送一般多久能到」）
都**保持原样**——它们记录的是当时实际测过/发生过什么，改字符串等于伪造证据。这与 `CHANGELOG` 历史条目、`RELEASE_*` / `PHASE_*_COMPLETION` / `supplychain/*.json` 同一条纪律，只是边界从「文件」细化到「段落性质」。

由此得一条与 §5 互补的教训：**「收敛为 0」只证明幂等，不证明落盘正确；而独立扫描的词表本身也可能不全**——`服务台` 在 P2 的词表里，`服务团队 / 服务助手` 不在，于是漏掉整整一层。扫描词表必须按**构词**而非**现成词**枚举（`服务X` 而不是只列 `服务台`）。

反向判据：**夹具自证**（测试自己写入、再断言自己写的内容原样返回）不算夹具内容——改它是纯 churn；只有字符串是**产品侧拥有的事实**时才随 P2b 迁移。本轮的实测边界：`tests/test_connectors_http.py` 的桩返回 `article_id` / `title` 并被 `assertEqual`（产品字段）、`tests/test_audit_fixes.py` 的桩返回 `status` 并被回读（产品状态串）→ 迁移；`tests/test_multilingual.py` / `tests/test_copilot.py` 自建文章标题、`tests/frontend/*.js` 的渲染输入 → 保留。另有三类**用户可见但不写进断言**的英文遗留（`turn_execution.py` 的 `CRM connector unavailable` / `Customer reference not resolvable in this tenant`、`AgentName.ORDER`、`order_id` 槽位名）刻意不改——它们是 P3/P4 的标识符面，提前改成中文/新词会让测试断言与实现分叉。

### 3.4 P2c 文案层补漏：`知识` 族的构词式漏网

T9 在映射表里写作「`知识库` → `策略库`」—— **一个现成词**。但 `知识` 在本域是**构词语素**：`知识文章 / 知识草稿 / 知识缺口 / 知识标签 / 知识管理员 / 知识引用 / 知识来源` 全是它的复合词。只迁 `知识库` 等于只迁了一个词形，于是策略库页面自己写着「新建**知识**草稿」，同屏两套术语。

§3.3 已经总结出这条教训（「扫描词表必须按**构词**而非**现成词**枚举」），但当时只用于扫描，没有回头补 P2 自己的词表。P2c 补上：

| 旧 | 新 | 说明 |
| --- | --- | --- |
| 知识文章 | 策略文章 | T9 的对象本身（`knowledge_articles → policy_articles`） |
| 知识草稿 | 策略草稿 | 同上（`draft` 状态的策略文章） |
| 知识缺口 | 策略缺口 | P3a 已把路径改为 `/api/supervisor/policy-gaps` |
| 知识标签 | 策略标签 | 策略文章的 `tags` 字段 |
| 知识管理员 | 策略管理员 | 持写权限的策略维护角色 |
| 知识引用 / 知识来源 | 策略引用 / 策略来源 | inspector 的 citation 面板 |
| 内部知识 | 内部策略 | `i18n.js` 的 `misc.internal_knowledge`（**值**迁移，**键**属 P5） |
| 知识检索 / 知识回流 / 知识匹配 / 知识命中 / 知识种子 / 知识运营 | 策略检索 / 策略回流 / 策略匹配 / 策略命中 / 策略种子 / 策略运营 | 文档与评测集描述里的零散复合词 |

**命中面：27 文件 / 79 处** —— 产品面 16 文件 49 处（原生 `index.html` 8、`knowledge-view.js` 14、`quality-panel.js` 4、`inspector.js` 3、`app.js` 2、`i18n.js` 1、`commands.js` 1；React 岛 `knowledge-island.jsx` 5、`knowledge/components.jsx` 3、`knowledge/domain.js` 1、`inspector/sections.jsx` 3、`inspector-island.jsx` 2、`inspector/helpers.jsx` 1、`command-palette-island.jsx` 1）；断言产品文案的测试 4 文件 15 处；评测集描述 2 文件 4 处；活跃文档 6 文件 9 处；`desktop/` 注释 1 文件 2 处。

**判定后刻意保留（不改）**：

| 残留 | 位置 | 理由 |
| --- | --- | --- |
| `知识条目` | `tests/test_multilingual.py` | 自证夹具载荷：POST `/api/policy` 的 `content`，断言只到 422，该字符串不出现在任何断言里（§3.2「只作为不透明载荷穿过被测代码」） |
| `催单回复` / `退款指引` / `您的订单正在加急处理。` / `退款将在 3 个工作日内到账。` | `desktop/verify_composer_tools_desktop.py`、`frontend/src/islands/composer-island.test.jsx` | 同上：**自建 canned 载荷**。desktop 脚本经 `page.evaluate` 直接 POST，断言的是 `shortcut`（`cudan<random>`）而非 `title`/`body`，两个字段从不被回读 |
| `#knowledgeSearch` / `.knowledge-article` / `knowledgeReactIsland` 等 | 全仓 | DOM id / class / 模块名，属 P5 的标识符面 |

**`commands.js` 的检索别名（单独判定，结论是改）**：`{ id: "view.knowledge", label: "策略库", keywords: ["knowledge", "知识"] }` —— `label` 在 P2 已迁为「策略库」，`keywords` 却留着旧词，**同一行内 label 已迁、keywords 未迁**，正是本节的漏网模式。另有一处**真实可用性缺陷**：`keywords` 里没有「策略」，于是用户按新术语搜不到该入口、按已废弃的旧术语反而能搜到。改为 `["knowledge", "策略"]`（英文段与 `id` 一致，保留）。

**P2c 与 §3.3 的关系**：§3.3 是「机械替换收敛后另做独立扫描」发现的缺口，P2c 是「把那条教训反向应用到**已发布**的词表上」的结果。两次都指向同一件事 —— **词表的完备性不能靠「脚本收敛为 0」证明，只能靠按语素枚举 + 全仓范围。**

## 4. 明确不动的标识符

这些名称不含业务域信号，或改动会引入不成比例的连带风险，**保持原样**：

| 标识符 | 原因 |
| --- | --- |
| `helix-server`（桌面 sidecar 二进制） | 无语义信号；改名会连带 PyInstaller spec、`src-tauri/src/supervisor.rs`、CI 冒烟测试与桌面资源路径 |
| `helix_client`（Python SDK 包） | 无语义信号 |
| `X-Helix-Timestamp`（渠道签名请求头） | 请求头改名是 API 破坏性变更 |
| `HelixClient`（SDK 类） | 无语义信号 |
| `queue` / `channel` / `tenant` / `message` / `audit` / `retention` | 通用软件词汇，新域中天然成立 |

## 5. 迁移阶段

| 阶段 | 范围 | 状态 |
| --- | --- | --- |
| P1 叙事层 | 产品名、`pyproject.toml`、`package.json`、`README.md`、本契约 | **已完成**（2.23.0） |
| P2 文案层 | 前端与后端用户可见文案、`i18n.js`、耦合测试断言、活跃文档、README 截图重捕获；**含第二遍补漏**（提交端入口与审核员面残留客服词，见 §3.1） | **已完成**（2.24.0） |
| P2b 内容夹具层 | 种子知识库正文、预置结论正文、提交端演示对话、来源查询工具状态串（`运输中`/`已出库`）、`golden/set.json` + `golden/adversarial.json`、耦合测试夹具与演示脚本（见 §3.2 边界判定） | **已完成**（2.25.0） |
| P2c 文案层补漏 | `知识` 族的**构词**残留（P2 只迁了映射表列出的 `知识库`）：产品面 16 文件、断言、评测集描述、活跃文档、`commands.js` 检索别名、`desktop/` 注释；见 §3.4 | **已完成**（2.27.0） |
| P3 模块与路由 | 领域模块改名、API 路径改名、openapi 快照、SDK 同步 | **P3a 已完成**（2.26.0，T4 / T6–T9 / T12 / T13 共 24 路径）；**P3b 已完成**（2.28.0，T5 `conversation` → `review_case`：模块、28 操作路径面、24 组弃用条目与 12 个月窗口；顺带修复 P3a 漏改的 `/api/conversations/{id}/messages/{message_id}/knowledge-draft` 尾段） |
| P4 数据层 | `core_schema.py` + v01–v48 一致改写、`_detect_legacy_version` | **已完成**（2.29.0）：表名 T5/T6/T7/T9/T12/T13/T14 与列名 T1–T4/T10/T11/T14 全量改写，迁移文件名同步改写且**链长仍为 48**（无新增 v49）；wire 契约经 `app/db/_util.py` 的 `to_wire_row()` 在响应组装点还原，见 §7 |
| P5 收口 | 测试函数/文件名、视觉基线重锚、遗留 `.bak` 清理、README 迁移横幅移除 | **已完成**（2.29.0，与 P4 同批次）：测试函数/文件名按 T1–T14 改名、`app/database.py.bak` 删除（`app/main.py.bak` 按 R6 保留）、README 迁移横幅移除；视觉基线**无需重锚**（P4/P5 无 UI 变更，`visual_gate` 四面 0.00% diff） |

**P2b 为何单列**：P2 执行时的取证表明，「订单查询」不是展示文案而是**真功能**——`app/agents.py` 有订单意图分类器（关键词元组含 `订单/物流/快递/到哪`）与 `ORD-*` 查询链路，`app/db/tenancy.py` 有客服 FAQ 种子正文，而 `golden/set.json` 的断言包含 `in_content: ["运输中"]`、`["已出库"]` 这类**由种子数据决定的中文子串**，CI 又有 `evaluate.py --min-pass-rate 1.0` 与 23/23 对抗门禁。三者在语义上是一个整体：只改文案会导致「UI 说审核单、分类器仍按订单匹配、评测集仍断言运输中」。因此把它们合并为一个独立阶段，一次改完、一次验证。

**T5 建议作为最后一步单独收口**：它独占全部改动量的约 70%，但增量简历价值最低（`conversation` / `messages` 是通用软件词汇）。T1–T4、T6–T14 落地后，仓库已不再"一眼是客服"。

### 5.1 P4/P5 完成后的两处**未收口面**（已登记，非遗漏）

P4/P5（2.29.0）按计划把「数据库对象 + 测试/基线」迁完，但下面两处**明确不在该批次范围**内，且经实测确认仍是旧域命名——它们**不是遗漏，是已登记的后续项**：

| 面 | 实测状态（2.29.0 时点） | 为何未纳入 P5 |
| --- | --- | --- |
| DOM id / class / 模块名 | `app/static/index.html` 仍有 `#knowledgeSearch` / `.knowledge-article`；`frontend/src/islands/*` 同名残留；`i18n.js` 的 **键** `misc.internal_knowledge` 未改（§3.4 已把**值**迁为「内部策略」） | 改 DOM 标识符会牵动 `visual_gate` / `frontend_gate` / `readme_screenshots` / `ui_accessibility` 四个脚本与视觉基线，属**独立重锚批次**的成本，与「测试/基线收口」不是一回事 |
| `docs/api/reference.md` | 文档化 141 处 vs 快照 181 路径 / 215 操作，**既存漂移先于域迁移**（P3a 时点为 107 vs 133） | 该文件由 `scripts/api_docs.py` 从快照生成，漂移与域迁移**正交**；在本批次重生成会把 3000+ 行无关 diff 混进域迁移提交里，破坏「一次迁移一个理由」的审阅边界 |

这两项与 §4「明确不动的标识符」性质不同：§4 是**判定为不改**，本节是**判定为改、但排在域迁移之后**。

**P2 的两条方法学教训**（后续阶段直接复用）：

1. **「脚本复跑收敛为 0」不等于「落盘正确」**。第一遍 `转接人工客服 → 转接人工复核` 与 `人工客服 → 人工复核` 在同一次运行里**字段重叠**，产出 `转接人工复核复核`。补上哨兵守护后**不再新生**，但守护会把 `转接人工复核` 保护起来、把多出来的「复核」当正文留下——脚本对最终树复跑报 `0 files, 0 replacements`，缺陷却被冻结在文件里。收敛判据只能证明**幂等**，正确性必须另做**独立扫描**（本轮为「残留客服词逐条判定」，见 §3.1）。
2. **机械替换必须配「最长优先 + 语义守护」两张表**。P2 首轮 41 处误伤全部来自同形异指：`客户端`（client）、认证语境的 `会话`（session）、`工单系统`（ITSM）、动名同形的 `解决`。守护表按 NUL 哨兵占位后还原；已成为误伤的落盘内容无法靠守护救回，另设反向回修表显式修正一次。

## 6. 历史档案的处理约定

以下文件是**已发布事实的日期化记录**，迁移时**不改写**，仅在需要时新增条目：

- `CHANGELOG.md` 的历史条目
- `docs/RELEASE_*.md`、`docs/PHASE_*_COMPLETION.md`、`docs/PROGRESS_REPORT_*.md`
- `supplychain/*.json`（威胁模型增量、演练台账：带日期的治理记录）

理由：改写历史记录等同于伪造事实，与本项目"如实记录"的交付纪律冲突。

**边界说明（P2 确立）**：`docs/adr/*.md` 与内部评估/分析报告（`GATE_B_EVALUATION.md`、`REACT_ISLAND_PERF_ANALYSIS.md`、`runbooks/M0_HARDENING_ACCEPTANCE.md`）**随迁移更新术语**，但结论、数字、日期与状态一律不动。区分标准是**对外发布**与否：`CHANGELOG` 与 `RELEASE_*` 是绑定版本号的对外档案，改写会让外部读者看到的既有事实与仓库不一致；ADR 与内部报告是工程工作文档，术语规范化属于正常维护。

`docs/DOMAIN.md` 与 `docs/DOMAIN_MIGRATION_PLAN.md` **自身必须排除在任何批量替换之外**：它们以「客户 → 提交方」这种**旧词→新词**形式书写，被替换会把映射表两侧改成同一个词，摧毁唯一的事实来源。

## 7. wire 契约：P4 之后「改名在源、别名在边界」

P4 把数据库**表名与列名**迁到新域词之后，仓库里同时存在**两套命名**：DB 层用新名，API 层用旧名。这不是过渡态残留，而是 `docs/API_POLICY.md`「响应体只增不改」的**必要代价**——HTTP 路径、响应体键、请求模型字段、SDK 方法签名一律保持旧键名，否则所有现存调用方立即破坏。

### 7.1 机制

`app/db/_util.py` 定义映射表 `_COLUMN_TO_WIRE` 与还原函数 `to_wire_row()`，在**响应组装点**把 DB 新列名还原为 wire 旧键名：

| DB 列（新） | wire 键（旧） | 词表 |
| --- | --- | --- |
| `submitter_name` | `customer_name` | T1 |
| `submitter_ref` | `customer_ref` | T2 |
| `risk_category` | `intent` | T10 |
| `assigned_reviewer` | `assigned_agent` | T4 |
| `decided_at` | `resolved_at` | T11 |
| `appeal_id` | `ticket_id` | T6 |
| `review_case_id` | `conversation_id` | T5 |
| `source_review_case_id` | `source_conversation_id` | T5 |

对应地，**表名不参与 wire 契约**：`review_cases` / `appeals` / `qa_spot_checks` 等新表名不出现在任何 HTTP 响应里，故无需别名。

### 7.2 判据：一个出口是不是「wire 面」

`to_wire_row` 的漏应用与误应用**都不产生静态错误**，唯一的判据是**该出口的字节有没有离开进程**：

| 面 | 判定 | 依据 |
| --- | --- | --- |
| HTTP 响应组装点、webhook 载荷、SDK 可见字段 | **是 wire 面** → 必须 `to_wire_row` | 有外部消费者 |
| `app/retention.py:38-49` 的 `PII_FIELDS` | **是 wire 名** → 导出前必须先还原 | 它是**脱敏器的输入契约**，按 wire 名登记；裸 DB 行嵌入导出 JSON 会因列名已改而**漏脱敏**（安全缺陷，非风格问题） |
| `app/retention.py:232` 的审计归档文档 | **不是 wire 面** → 不还原 | `app/audit_chain.py:111-125` 的 `required_fields` 依赖 `conversation_id`，内部序列化协议自洽 |
| `app/routers/system.py` 的 `_REPLICATED_SYNTHETIC` / `_REPLICATED_COLUMNS` | **不是 wire 面** → 用新列名 | 跨区复制是 `region_replication.py` 的 cell-to-cell 内部协议，逐字节转发源行，无外部消费者 |
| `app/routers/attachments.py` 的 Form/Query 参数、`app/routers/admin.py:634` 的查询参数 | **是 wire 名** → 保持 `conversation_id` / `customer_ref` | URL 契约，改名会让现存调用 422 |

### 7.3 遗留的守护缺口（R8④，未实施）

上述判据**不在任何现有守护的射程内**：漏应用表现为「客户端静默取不到值」、误应用表现为「内部协议被改坏」，两者都不会让测试变红。建议补一条**契约常量断言**——`PII_FIELDS` 这类按 wire 名登记的常量，其键名必须等于 `_COLUMN_TO_WIRE` 的**像集**与「未改名原样列名」之并集的子集。本版未实施，登记于此。
