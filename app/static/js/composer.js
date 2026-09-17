/**
 * Helix Support — composer surface (ROADMAP §41.6 / ARC-001): drafts, claim
 * renewal, canned macros and the AI copilot bar. app.js keeps thin
 * delegating wrappers and calls configure() once with its singletons.
 *
 * The copilot tool cores live in js/copilot-tools.js (ROADMAP H02) and the
 * command/receipt guards in js/composer-command.js; both are re-exported here
 * so every existing call site and test keeps its import path.
 */

import { clearPendingAttachments, pendingIds } from "./attachment.js?v=1.4.0";
import {
  clearDraft,
  draftKey,
  draftTtlMs,
  draftsEnabled,
  loadDraft,
  pruneExpiredDrafts,
  saveDraft,
} from "./drafts.js?v=1.4.0";
import {
  beginCommand,
  bumpDraftVersion,
  clearSendKey,
  configure as configureCommand,
  isCurrent,
  sendKeyFor,
} from "./composer-command.js?v=1.4.0";
import {
  COMPOSER_COPILOT_EVENT,
  applyCopilotTone,
  configure as configureCopilotTools,
  fetchCopilotSuggestions,
  loadCopilotKnowledge,
  publishCopilot,
  resetCopilot,
  setCopilotStatus,
  suggestionAt,
} from "./copilot-tools.js?v=1.4.0";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers) — the single
 *  injection point for the whole composer surface: the extracted
 *  js/copilot-tools.js cores and the js/composer-command.js contract receive
 *  the same bundle, so a caller that configures the composer configures all
 *  of it. */
export function configure(deps) {
  ctx = deps;
  configureCopilotTools(deps);
  configureCommand(deps);
}

// Draft persistence lives in js/drafts.js (re-exported for the namespace).
export { clearDraft, draftKey, draftTtlMs, draftsEnabled, loadDraft, pruneExpiredDrafts, saveDraft } from "./drafts.js?v=1.4.0";
// Pending attachments are part of the send lifecycle (the composer owns the
// attachment bar). Re-exported for the namespace — `configure` included, and
// aliased because the `?v=` query is part of a module's URL: a consumer that
// resolves the bare path would configure a *second* instance whose state the
// composer never reads.
export {
  clearPendingAttachments,
  configure as configureAttachments,
  pendingIds,
  uploadPendingAttachment,
} from "./attachment.js?v=1.4.0";
// The command/receipt contract (js/composer-command.js).
export {
  beginCommand,
  bumpDraftVersion,
  clearSendKey,
  draftVersionFor,
  isCurrent,
  isDraftUnchanged,
  reset,
  sendKeyFor,
} from "./composer-command.js?v=1.4.0";
// Copilot tool cores (js/copilot-tools.js).
export {
  COMPOSER_COPILOT_EVENT,
  applyCopilotTone,
  fetchCopilotSuggestions,
  loadCopilotKnowledge,
  resetCopilot,
  setCopilotStatus,
  suggestionAt,
} from "./copilot-tools.js?v=1.4.0";

export function scheduleClaimRenewal(detail) {
  if (ctx.state.claimRenewTimer) {
    window.clearTimeout(ctx.state.claimRenewTimer);
    ctx.state.claimRenewTimer = null;
  }
  const conversation = detail?.conversation;
  if (!conversation?.claim_active || conversation.claimed_by !== ctx.state.me?.actor_id) return;
  if (!conversation.claim_expires_at) return;
  const expires = new Date(conversation.claim_expires_at).getTime();
  const delay = Math.max(15000, expires - Date.now() - 120000);
  ctx.state.claimRenewTimer = window.setTimeout(async () => {
    if (ctx.state.selectedId !== conversation.id) return;
    try {
      await ctx.api(`/api/conversations/${encodeURIComponent(conversation.id)}/claim`, {
        method: "POST",
      });
      if (ctx.state.selectedId === conversation.id) await ctx.loadDetail(conversation.id);
    } catch {
      // best-effort claim renewal
    }
  }, delay);
}

export function hideMacroSuggest() {
  ctx.state.macroOpen = false;
  if (ctx.els.macroSuggest) {
    ctx.els.macroSuggest.hidden = true;
    ctx.els.macroSuggest.innerHTML = "";
  }
}

export function renderMacroSuggest(query) {
  if (!ctx.els.macroSuggest || !ctx.canOperate()) {
    hideMacroSuggest();
    return;
  }
  const needle = (query || "").toLowerCase();
  const matches = ctx.state.cannedResponses
    .filter((item) => {
      const shortcut = (item.shortcut || "").toLowerCase();
      const title = (item.title || "").toLowerCase();
      return !needle || shortcut.includes(needle) || title.includes(needle);
    })
    .slice(0, 6);
  if (!matches.length) {
    hideMacroSuggest();
    return;
  }
  ctx.state.macroOpen = true;
  ctx.els.macroSuggest.hidden = false;
  ctx.els.macroSuggest.innerHTML = matches
    .map(
      (item) =>
        `<button class="macro-option" type="button" role="option" data-macro-id="${ctx.escapeHtml(item.id)}"><strong>${ctx.escapeHtml(item.title)}</strong><span>/${ctx.escapeHtml(item.shortcut || "—")}</span></button>`,
    )
    .join("");
}

export function applyMacroFromSuggest(responseId) {
  const macro = ctx.state.cannedResponses.find((item) => item.id === responseId);
  if (!macro) return;
  const value = ctx.els.operatorInput.value;
  const match = value.match(/(^|\s)\/([^\s]*)$/);
  if (match) {
    const start = value.slice(0, value.length - match[0].length + (match[1] ? match[1].length : 0));
    ctx.els.operatorInput.value = `${start}${macro.body}`;
  } else {
    ctx.els.operatorInput.value = macro.body;
  }
  hideMacroSuggest();
  // ROADMAP H02: the macro replaced the draft, so an in-flight rewrite
  // computed from the previous text is stale.
  bumpDraftVersion(ctx.state.selectedId);
  saveDraft(ctx.state.selectedId, ctx.els.operatorInput.value);
  ctx.els.operatorInput.focus();
  void ctx.api(`/api/canned-responses/${encodeURIComponent(responseId)}/use`, { method: "POST" }).catch(
    () => {},
  );
}

/** Paint the canned chips (browser); island mode republishes state. */
export function renderCannedResponses() {
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    window.HelixModules?.composerIslandBridge?.publishComposerState?.();
    return;
  }
  if (!ctx.els.cannedList) return;
  if (!ctx.state.cannedResponses.length) {
    ctx.els.cannedList.innerHTML = '<span class="canned-empty">暂无快捷回复</span>';
    return;
  }
  ctx.els.cannedList.innerHTML = ctx.state.cannedResponses
    .slice(0, 8)
    .map(
      (item) =>
        `<button class="canned-chip" type="button" data-macro-id="${ctx.escapeHtml(item.id)}" title="${ctx.escapeHtml(item.body)}">${ctx.escapeHtml(item.title)}${item.shortcut ? ` /${ctx.escapeHtml(item.shortcut)}` : ""}</button>`,
    )
    .join("");
}

export async function loadCannedResponses({ force = false } = {}) {
  if (!ctx.canOperate()) {
    ctx.state.cannedResponses = [];
    publishState();
    return;
  }
  if (!force && ctx.state.cannedLoadedAt && Date.now() - ctx.state.cannedLoadedAt < 120000) return;
  try {
    ctx.state.cannedResponses = await ctx.api("/api/canned-responses");
    ctx.state.cannedLoadedAt = Date.now();
  } catch {
    ctx.state.cannedResponses = [];
  }
  publishState();
}

/** Usage tracking for island-applied macros (best-effort, like legacy). */
export async function recordMacroUse(responseId) {
  try {
    await ctx.api(`/api/canned-responses/${encodeURIComponent(responseId)}/use`, { method: "POST" });
  } catch {
    // usage tracking is best-effort
  }
}

export function renderCopilot(detail) {
  const conversation = detail.conversation;
  const human = ["waiting_human", "human_active"].includes(conversation.status);
  const visible = human && ctx.canOperate();
  // Island mode: the composer island derives the copilot/canned/attachment
  // tool visibility from the composer state snapshot; the knowledge load and
  // reset lifecycle stay here.
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    if (!visible) {
      resetCopilot();
      publishCopilot({ suggestions: [], knowledge: [], status: "" });
    } else if (ctx.state.lastCopilotConv !== conversation.id) {
      void loadCopilotKnowledge();
    }
    publishState();
    return;
  }
  if (!ctx.els.copilotBar) return;
  ctx.els.copilotBar.hidden = !visible;
  if (ctx.els.copilotBar.hidden) {
    resetCopilot();
    return;
  }
  if (ctx.state.lastCopilotConv !== conversation.id) void loadCopilotKnowledge();
}

/** Republish the composer state snapshot to the island (no-op outside
 * island mode; the bridge module owns the payload). */
function publishState() {
  if (typeof window === "undefined" || !window.__HELIX_ISLAND_MODE__) return;
  window.HelixModules?.composerIslandBridge?.publishComposerState?.();
}

/** Send an operator message (shared by the legacy form and the React island
 *  bridge). Reads nothing from the DOM — the caller passes the content.
 *
 *  ROADMAP H02: the attempt carries a stable idempotency key, so a retry after
 *  a lost response resolves to the message the server already stored instead
 *  of sending the customer a second copy. Only the originating conversation's
 *  composer is cleared — a late confirmation for A must never wipe the draft
 *  the operator is typing in B — and a failed send keeps the text and the
 *  pending attachments retryable. */
export async function sendOperatorMessage(content) {
  const conversationId = ctx.state.selectedId;
  if (!conversationId) return;
  const text = String(content || "").trim();
  if (!text) return;
  hideMacroSuggest();
  const ticket = beginCommand("operator-send", conversationId);
  // Backlog (语音/富媒体消息): include this conversation's pending uploads.
  const pendingIds_ = pendingIds(conversationId);
  const sendKey = sendKeyFor(conversationId, text, pendingIds_);
  ctx.setFormBusy(ctx.els.operatorForm, true);
  try {
    const body = { content: text };
    if (pendingIds_.length) body.attachment_ids = [...pendingIds_];
    await ctx.api(`/api/conversations/${encodeURIComponent(conversationId)}/operator-messages`, {
      method: "POST",
      headers: { "Idempotency-Key": sendKey },
      body: JSON.stringify(body),
    });
    // Confirmed: the receipt has done its job and the attempt is complete, so
    // deliberately sending the same text again is a new attempt.
    clearSendKey(conversationId, text, pendingIds_);
    clearPendingAttachments(conversationId);
    clearDraft(conversationId);
    if (isCurrent(ticket)) {
      ctx.els.operatorInput.value = "";
      // The sent text is no longer the draft: an in-flight rewrite computed
      // from it must not paste it back into the now-empty box.
      bumpDraftVersion(conversationId);
      await ctx.loadDetail(conversationId);
    }
    void ctx.refreshAll({ silent: true, refreshDetail: false });
  } catch (error) {
    ctx.showToast(error.message, true);
  } finally {
    ctx.setFormBusy(ctx.els.operatorForm, false);
  }
}

/**
 * Bind the composer DOM (exactly once, at app.js load): operator message
 * submit, copilot suggestion chips, tone rewrite and knowledge picks.
 */
export function bindComposer() {
  if (!ctx?.els) return false;
  if (ctx.els.operatorForm) {
    ctx.els.operatorForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!ctx.state.selectedId) return;
      const content = ctx.els.operatorInput.value.trim();
      if (!content) return;
      await sendOperatorMessage(content);
    });
  }
  if (ctx.els.copilotSuggestBtn) {
    ctx.els.copilotSuggestBtn.addEventListener("click", () => void fetchCopilotSuggestions());
  }
  if (ctx.els.copilotTone) {
    ctx.els.copilotTone.addEventListener("change", () => {
      const tone = ctx.els.copilotTone.value;
      ctx.els.copilotTone.value = "";
      if (tone) void applyCopilotTone(tone);
    });
  }
  if (ctx.els.copilotSuggestions) {
    ctx.els.copilotSuggestions.addEventListener("click", (event) => {
      const button = event.target.closest(".copilot-suggestion");
      if (!button) return;
      const content = suggestionAt(button.dataset.index);
      if (content) {
        ctx.els.operatorInput.value = content;
        bumpDraftVersion(ctx.state.selectedId);
        ctx.els.operatorInput.focus();
      }
    });
  }
  if (ctx.els.copilotKnowledge) {
    ctx.els.copilotKnowledge.addEventListener("click", (event) => {
      const button = event.target.closest(".copilot-kb-item");
      if (!button) return;
      ctx.els.operatorInput.value = button.dataset.title || "";
      bumpDraftVersion(ctx.state.selectedId);
      ctx.els.operatorInput.focus();
    });
  }
  return true;
}
