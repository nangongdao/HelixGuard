/**
 * Helix Guard — appeal view (ROADMAP §41.6 / ARC-001).
 *
 * Ticket list/detail/state machine and the workspace queue/tickets tab.
 * Extracted from the legacy app.js; app.js keeps thin delegating wrappers
 * with identical names/signatures, so the running UI behaviour is unchanged.
 * app.js calls configure() once at load time with its singletons.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

const TICKET_STATUS_NAMES = { open: "待处理", in_progress: "处理中", closed: "已关闭" };
let ticketBadgeCache = {};

export async function enrichTicketBadge(ticketId) {
  if (!ticketId || !ctx.els.appealBadge) return;
  if (ticketBadgeCache[ticketId]) {
    ctx.els.appealBadge.textContent = `申诉单 ${ticketId} · ${ticketBadgeCache[ticketId]}`;
    return;
  }
  try {
    const appeal = await ctx.api(`/api/appeals/${encodeURIComponent(ticketId)}`);
    const status = TICKET_STATUS_NAMES[appeal.status] || appeal.status || "";
    ticketBadgeCache[ticketId] = status;
    if (ctx.state.selectedId && ctx.els.appealBadge) {
      ctx.els.appealBadge.textContent = `申诉单 ${ticketId} · ${status}`;
    }
  } catch {
    /* badge stays at the id-only form */
  }
}

export async function convertToTicket() {
  const conversationId = ctx.state.selectedId;
  if (!conversationId || !ctx.canOperate()) return;
  const subject = window.prompt("转申诉单主题（长周期问题描述）", "问题跟进");
  if (subject === null || !subject.trim()) return;
  try {
    await ctx.api("/api/appeals", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId, subject: subject.trim() }),
    });
    await ctx.loadDetail(conversationId);
  } catch (error) {
    window.alert(`转申诉单失败：${error.message || error}`);
  }
}

let ticketListLoaded = false;
// 申诉单详情「跳转关联审核单」窗口内抑制 runRefresh 自动选第一条审核单的计数标志
// (jumpToTicketConversation 独占);>0 时 else-if 的 auto-select 被跳过。
let suppressAutoSelect = 0;
let activeTicketId = null;

export function switchWorkspaceTab(field) {
  if (!ctx.els.wsTabQueue || !ctx.els.wsTabAppeals) return;
  const isAppeals = field === "appeals";
  if (ctx.els.queuePane) ctx.els.queuePane.dataset.mode = isAppeals ? "appeals" : "queue";
  // Island mode: the workspace tabs island owns the tablist; report the new
  // active tab so the island reconciles (also covers programmatic switches).
  window.dispatchEvent(
    new CustomEvent("helix-workspace-tab-changed", { detail: { field } }),
  );
  if (ctx.els.wsTabQueue) {
    ctx.els.wsTabQueue.classList.toggle("is-active", !isAppeals);
    ctx.els.wsTabQueue.setAttribute("aria-selected", String(!isAppeals));
  }
  if (ctx.els.wsTabAppeals) {
    ctx.els.wsTabAppeals.classList.toggle("is-active", isAppeals);
    ctx.els.wsTabAppeals.setAttribute("aria-selected", String(isAppeals));
  }
  if (ctx.els.appealPane) ctx.els.appealPane.hidden = !isAppeals;
  if (isAppeals) {
    if (!ticketListLoaded) void loadTickets();
    else refreshTicketsList();
  } else {
    closeTicketDetail();
    void ctx.refreshAll({ silent: true });
  }
}

/** Hide the appeal detail, restoring the conversation/empty mount point. */
export function closeTicketDetail() {
  if (activeTicketId === null) return;
  activeTicketId = null;
  if (ctx.els.appealDetailView) ctx.els.appealDetailView.hidden = true;
  if (ctx.state.selectedId && ctx.els.reviewCaseView) {
    ctx.els.reviewCaseView.hidden = false;
    if (ctx.els.emptyState) ctx.els.emptyState.hidden = true;
  } else {
    if (ctx.els.reviewCaseView) ctx.els.reviewCaseView.hidden = true;
    if (ctx.els.emptyState) ctx.els.emptyState.hidden = false;
  }
}

export async function loadTickets() {
  // D3 take-over: in the desktop shell the React appeal island owns the list
  // (#appealList is yielded and hidden by the island loader). Rendering into a
  // hidden container would still duplicate .appeal-row nodes in the DOM and
  // fire a redundant fetch, so yield here instead.
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) return;
  const status = ctx.els.appealStatusFilter?.value || "";
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  try {
    const tickets = await ctx.api(`/api/appeals${query}`);
    ticketListLoaded = true;
    renderTicketList(tickets || []);
  } catch (error) {
    if (ctx.els.appealList) {
      ctx.els.appealList.innerHTML = `<p class="appeal-empty">加载失败：${ctx.escapeHtml(error.message || error)}</p>`;
    }
  }
}

export function refreshTicketsList() {
  void loadTickets();
}

export function renderTicketList(tickets) {
  if (!ctx.els.appealList) return;
  if (!tickets.length) {
    ctx.els.appealList.innerHTML = `<p class="appeal-empty">暂无申诉单</p>`;
    return;
  }
  ctx.els.appealList.innerHTML = tickets
    .map((t) => {
      const statusName = TICKET_STATUS_NAMES[t.status] || t.status;
      const high = t.priority === "high" ? " is-high" : "";
      const ref = t.customer_ref ? ` · ${ctx.escapeHtml(t.customer_ref)}` : "";
      return `<button type="button" class="appeal-row" data-appeal-id="${ctx.escapeHtml(t.id)}">
        <span class="appeal-row-main">
          <span class="appeal-subject">${ctx.escapeHtml(t.subject)}</span>
          <span class="appeal-meta">${ctx.escapeHtml(t.customer_name || "—")}${ref}</span>
        </span>
        <span class="appeal-row-side">
          <span class="priority-pill${high}">${t.priority === "high" ? "高优" : "普通"}</span>
          <span class="status-pill">${ctx.escapeHtml(statusName)}</span>
          <span class="appeal-meta">${ctx.escapeHtml(ctx.formatTime(t.updated_at))}</span>
        </span>
      </button>`;
    })
    .join("");
}

export async function openTicketDetail(ticketId) {
  activeTicketId = ticketId;
  try {
    const detail = await ctx.api(`/api/appeals/${encodeURIComponent(ticketId)}`);
    // 过期响应守卫:await 期间用户已切换另一申诉单或关闭详情(activeTicketId
    // 变化),旧票响应不得覆盖当前视图,也不得把 null 传给 /api/appeals/null。
    if (activeTicketId !== ticketId) return;
    renderTicketDetail(detail);
    if (ctx.els.appealDetailView) ctx.els.appealDetailView.hidden = false;
    if (ctx.els.reviewCaseView) ctx.els.reviewCaseView.hidden = true;
    if (ctx.els.emptyState) ctx.els.emptyState.hidden = true;
  } catch (error) {
    if (activeTicketId === ticketId) {
      activeTicketId = null;
      window.alert(`申诉单加载失败：${error.message || error}`);
    }
  }
}

export function renderTicketDetail(t) {
  const statusName = TICKET_STATUS_NAMES[t.status] || t.status;
  if (ctx.els.appealDetailTitle) ctx.els.appealDetailTitle.textContent = `${t.id} · ${t.subject}`;
  if (ctx.els.appealDetailStatus) {
    ctx.els.appealDetailStatus.textContent = statusName;
    ctx.els.appealDetailStatus.classList.toggle("is-appeal-closed", t.status === "closed");
  }
  if (ctx.els.appealDetailPriority) {
    ctx.els.appealDetailPriority.textContent = t.priority === "high" ? "高优先级" : "普通优先级";
    ctx.els.appealDetailPriority.classList.toggle("is-high", t.priority === "high");
    ctx.els.appealDetailPriority.hidden = false;
  }
  if (ctx.els.appealDetailSubtitle) {
    const parts = [t.customer_name || "—"];
    if (t.customer_ref) parts.push(t.customer_ref);
    if (t.assigned_agent) parts.push(`负责人：${t.assigned_agent}`);
    parts.push(`创建 ${ctx.formatTime(t.created_at)}`);
    if (t.closed_at) parts.push(`关闭 ${ctx.formatTime(t.closed_at)}`);
    ctx.els.appealDetailSubtitle.textContent = parts.join(" · ");
  }
  if (ctx.els.appealDetailDescription) {
    if (t.description) {
      ctx.els.appealDetailDescription.hidden = false;
      ctx.els.appealDetailDescription.textContent = t.description;
    } else {
      ctx.els.appealDetailDescription.hidden = true;
    }
  }
  renderTicketConvs(t.conversations);
  renderTicketTransitions(t.status);
  const linkedThisConv = (t.conversations || []).some((c) => c.id === ctx.state.selectedId);
  if (ctx.els.appealLinkCurrent) ctx.els.appealLinkCurrent.hidden = !ctx.state.selectedId || linkedThisConv;
}

export function renderTicketTransitions(status) {
  if (!ctx.els.appealTransitions) return;
  const actions = {
    open: [["in_progress", "开始处理"], ["closed", "直接关闭"]],
    in_progress: [["closed", "关闭"]],
    closed: [["open", "重开"]],
  }[status] || [];
  ctx.els.appealTransitions.innerHTML = actions
    .map(
      ([next, label]) =>
        `<button type="button" class="button button-secondary appeal-transition" data-status="${ctx.escapeHtml(next)}">${ctx.escapeHtml(label)}</button>`,
    )
    .join("");
}

export function renderTicketConvs(convs) {
  if (!ctx.els.appealDetailConvs) return;
  if (!convs || !convs.length) {
    ctx.els.appealDetailConvs.innerHTML = `<h3 class="appeal-convs-title">关联审核单</h3><p class="appeal-empty">未关联审核单</p>`;
    return;
  }
  ctx.els.appealDetailConvs.innerHTML =
    `<h3 class="appeal-convs-title">关联审核单</h3>` +
    convs
      .map(
        (c) =>
          `<button type="button" class="appeal-conv-row" data-conv-id="${ctx.escapeHtml(c.id)}">
            <span class="appeal-meta">${ctx.escapeHtml(c.customer_name || "—")}</span>
            <span class="status-pill">${ctx.escapeHtml(ctx.statusLabel(c.status))}</span>
            <span class="appeal-meta">${ctx.escapeHtml(ctx.formatTime(c.updated_at))}</span>
          </button>`,
      )
      .join("");
}

export async function transitionActiveTicket(status) {
  // 入口拷贝:await 期间用户可能已关闭详情或切到另一申诉单,不能读模块级变量。
  const ticketId = activeTicketId;
  if (!ticketId) return;
  try {
    await ctx.api(`/api/appeals/${encodeURIComponent(ticketId)}/transition`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
    void openTicketDetail(ticketId);
    void refreshTicketsList();
  } catch (error) {
    window.alert(`申诉单状态变更失败：${error.message || error}`);
  }
}

export async function linkActiveTicketConversation() {
  const ticketId = activeTicketId;
  const conversationId = ctx.state.selectedId;
  if (!ticketId || !conversationId) return;
  try {
    await ctx.api(`/api/appeals/${encodeURIComponent(ticketId)}/link`, {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId }),
    });
    void openTicketDetail(ticketId);
  } catch (error) {
    window.alert(`关联失败：${error.message || error}`);
  }
}

export async function jumpToTicketConversation(conversationId) {
  // 先置 selectedId 再切回队列,并临时抑制 runRefresh 的自动选第一条:
  // 否则 switchWorkspaceTab 内部 silent 前台刷新会在 selectedId 为空/不在
  // 队列时抢开 conversation[0]。await selectConversation 覆盖整个加载窗口。
  ctx.state.selectedId = conversationId;
  suppressAutoSelect++;
  try {
    switchWorkspaceTab("queue");
    await ctx.selectConversation(conversationId);
  } finally {
    suppressAutoSelect--;
  }
}

/** Current auto-select suppression depth (read by app.js runRefresh). */
export function autoSelectSuppressed() {
  return suppressAutoSelect > 0;
}

/**
 * Bind the appeal workspace DOM (exactly once, at app.js load): convert
 * button, workspace tabs, list/detail/transition/link controls.
 */
export function bindTickets() {
  if (!ctx?.els) return false;
  if (ctx.els.appealBtn) {
    ctx.els.appealBtn.addEventListener("click", () => void convertToTicket());
  }
  if (ctx.els.wsTabQueue) ctx.els.wsTabQueue.addEventListener("click", () => switchWorkspaceTab("queue"));
  if (ctx.els.wsTabAppeals) {
    ctx.els.wsTabAppeals.addEventListener("click", () => switchWorkspaceTab("appeals"));
  }
  if (ctx.els.appealStatusFilter) {
    ctx.els.appealStatusFilter.addEventListener("change", () => void loadTickets());
  }
  if (ctx.els.appealList) {
    ctx.els.appealList.addEventListener("click", (event) => {
      const row = event.target.closest(".appeal-row");
      if (row) void openTicketDetail(row.dataset.appealId);
    });
  }
  if (ctx.els.appealBack) {
    ctx.els.appealBack.addEventListener("click", () => closeTicketDetail());
  }
  if (ctx.els.appealTransitions) {
    ctx.els.appealTransitions.addEventListener("click", (event) => {
      const button = event.target.closest(".appeal-transition");
      if (button) void transitionActiveTicket(button.dataset.status);
    });
  }
  if (ctx.els.appealLinkCurrent) {
    ctx.els.appealLinkCurrent.addEventListener("click", () => void linkActiveTicketConversation());
  }
  if (ctx.els.appealDetailConvs) {
    ctx.els.appealDetailConvs.addEventListener("click", (event) => {
      const row = event.target.closest(".appeal-conv-row");
      if (row) jumpToTicketConversation(row.dataset.convId);
    });
  }
  return true;
}