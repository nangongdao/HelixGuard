/**
 * Helix Guard — conversation detail assembly (app.js <500 slice 28).
 *
 * renderDetail is the conductor that paints one selected conversation across
 * every domain surface: the header (title/status/avatar/SLA), the action
 * buttons' visibility, the composer surfaces (island-mode aware), and the
 * fan-out to thread/inspector/summaries/copilot/attachments plus the claim
 * renewal and attachment-name follow-ups. Extracted verbatim from app.js;
 * every domain painter arrives through configure (actions/state) because
 * they are app.js- or module-scoped.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

export function renderDetail(detail) {
  const { state, els, actions } = ctx;
  state.detail = detail;
  const conversation = detail.conversation;
  const sla = actions.formatSla(conversation);
  els.emptyState.hidden = true;
  els.reviewCaseView.hidden = false;
  els.reviewCaseTitle.textContent = conversation.customer_name;
  els.reviewCaseStatus.textContent = actions.statusLabel(conversation.status);
  els.reviewCaseStatus.className = `status-pill ${conversation.status}`;
  actions.renderSubtitle(conversation);
  actions.renderLanguagePicker(conversation);
  els.submitterAvatar.textContent = [...conversation.customer_name][0]?.toUpperCase() || "?";
  els.threadContext.textContent = `${conversation.intent || "待识别"} · ${conversation.assigned_agent || "未分配"}`;
  els.threadSla.textContent = sla.text;
  els.threadSla.classList.toggle("is-breached", sla.breached);

  const resolved = conversation.status === "resolved";
  const human = ["waiting_human", "human_active"].includes(conversation.status);
  const claimedByMe = conversation.claim_active && conversation.claimed_by === state.me?.actor_id;
  els.claimBtn.hidden = resolved || claimedByMe || !ctx.canOperate();
  els.releaseBtn.hidden = !claimedByMe || !ctx.canOperate();
  if (els.assignBtn) els.assignBtn.hidden = resolved || !ctx.canOperate();
  els.acceptBtn.hidden = resolved || conversation.status === "human_active";
  els.resolveBtn.hidden = resolved;
  els.reopenBtn.hidden = !resolved;
  // Backlog (申诉单化): convert button hides once the conversation belongs to
  // a appeal; the badge shows the appeal id and, once fetched, its status.
  if (els.appealBtn) els.appealBtn.hidden = !ctx.canOperate() || Boolean(conversation.ticket_id);
  if (els.appealBadge) {
    if (conversation.ticket_id) {
      els.appealBadge.textContent = `申诉单 ${conversation.ticket_id}`;
      els.appealBadge.hidden = false;
      actions.scheduleIdle(() => actions.enrichTicketBadge(conversation.ticket_id));
    } else {
      els.appealBadge.hidden = true;
    }
  }
  if (els.watchBtn) els.watchBtn.hidden = resolved || !ctx.canReadConversations();
  if (resolved || !ctx.canReadConversations()) actions.stopWatching();
  // Island mode: #reviewerForm is yielded (hidden by the loader) and the
  // canned chips render island-side from the state snapshot — only the
  // not-yielded legacy surfaces (composer notice) toggle here. #noteForm
  // is island-owned too (inspector domain), so it never toggles here.
  if (window.__HELIX_ISLAND_MODE__) {
    els.composerNotice.hidden = !human;
  } else {
    els.reviewerForm.hidden = !human;
    els.composerNotice.hidden = !human;
    els.cannedBar.hidden = !human || !ctx.canOperate();
    els.noteForm.hidden = resolved;
  }
  actions.renderCannedResponses();
  if (human && ctx.canOperate()) {
    const draft = actions.loadDraft(conversation.id);
    if (!els.reviewerInput.value || els.reviewerInput.dataset.reviewCaseId !== conversation.id) {
      els.reviewerInput.value = draft;
    }
    els.reviewerInput.dataset.reviewCaseId = conversation.id;
  }
  const customerBusy = els.submitterForm.dataset.busy === "true";
  els.submitterInput.disabled = resolved || customerBusy;
  els.submitterForm.querySelector("button").disabled = resolved || customerBusy;
  els.submitterInput.placeholder = resolved ? "审核单已判定，请先重开" : "输入一条模拟待审内容…";
  actions.renderMessages(detail.messages);
  actions.renderInspector(detail);
  actions.renderSummaries(detail);
  actions.renderCopilot(detail);
  actions.renderAttachmentBar(detail);
  if (conversation.id) {
    actions.scheduleIdle(async () => {
      await actions.loadAttachmentNames(conversation.id);
      // Island mode: the thread island renders attachment chips from the
      // snapshot — republish so real names replace the id fallbacks.
      if (state.selectedId !== conversation.id) return;
      window.HelixModules?.thread?.publishThreadState?.({ messages: detail.messages });
    });
  }
  actions.scheduleClaimRenewal(detail);
}

export default { renderDetail };
