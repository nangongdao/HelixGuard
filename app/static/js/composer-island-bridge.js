/**
 * Helix Support — composer island bridge (D3 + tools slice)
 *
 * Routes the React composer island's interactions back to the legacy
 * composer lifecycle (drafts, operator send, copilot tools, canned macros,
 * pending attachments, retryable upload failures) and publishes
 * helix-composer-state snapshots with the tool data the island renders
 * (canned responses, pending attachments, failed attachments, canOperate).
 * Kept in its own module so composer.js stays under the 400-line gate.
 */

import {
  applyCopilotTone,
  clearDraft,
  fetchCopilotSuggestions,
  hideMacroSuggest,
  recordMacroUse,
  renderMacroSuggest,
  saveDraft,
  sendOperatorMessage,
} from "./composer.js?v=1.4.0";
import {
  dismissFailedAttachment,
  removePendingAttachment,
  retryFailedAttachment,
  uploadPendingAttachment,
} from "./attachment.js?v=1.4.0";
import { bumpDraftVersion, draftVersionFor } from "./composer-command.js?v=1.4.0";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/canOperate). */
export function configure(deps) {
  ctx = deps;
}

/** Publish the current composer state for the React island mirror. The
 * tools (copilot bar, canned chips, pending attachments) are gated on
 * human + canOperate exactly like their legacy counterparts. */
export function publishComposerState() {
  if (typeof window === "undefined" || !window.__HELIX_ISLAND_MODE__) return;
  const detail = ctx.state?.detail?.conversation;
  const resolved = Boolean(detail && detail.status === "resolved");
  const human = Boolean(detail && ["waiting_human", "human_active"].includes(detail.status));
  const canOperate = Boolean(ctx.canOperate?.());
  const meta = window.HelixModules?.attachments?.attachmentMetaSnapshot?.() || {};
  const conversationId = ctx.state?.selectedId;
  const pending = conversationId
    ? (window.HelixModules?.attachments?.pendingIds?.(conversationId) || []).map((id) => ({
        id,
        filename: meta[id]?.filename || id,
      }))
    : [];
  // ROADMAP H02 (2.19.0): failed uploads stay retryable. The File lives
  // legacy-side; the island mirrors only the token so its chips can route the
  // retry/dismiss actions back through the bridge.
  const failed = conversationId
    ? (window.HelixModules?.attachments?.failedAttachments?.(conversationId) || []).map(
        (entry) => ({
          token: entry.token,
          filename: entry.filename,
          error: entry.error,
        }),
      )
    : [];
  window.dispatchEvent(
    new CustomEvent("helix-composer-state", {
      detail: {
        resolved,
        human,
        customerBusy: ctx.els?.customerForm?.dataset?.busy === "true",
        operatorBusy: ctx.els?.operatorForm?.dataset?.busy === "true",
        operatorDraft: ctx.els?.operatorInput?.value || "",
        // ROADMAP H02 (2.19.0): the canonical draft's generation. A confirmed
        // send clears the draft *without changing the text* when it was the
        // first thing typed (the box was already empty), so a value snapshot
        // alone cannot tell "cleared" from "never set" — the mirror needs the
        // generation to notice it.
        operatorDraftRevision: draftVersionFor(conversationId),
        canOperate,
        cannedResponses: Array.isArray(ctx.state?.cannedResponses) ? ctx.state.cannedResponses : [],
        pendingAttachments: pending,
        failedAttachments: failed,
      },
    }),
  );
}

/**
 * Bind the island bridge (exactly once, at app.js load). The React composer
 * island renders mirrored forms + tool surfaces in the desktop shell (legacy
 * forms yielded + hidden); route its interactions back to the legacy
 * send/draft/copilot/attachment lifecycle.
 */
export function bindIslandBridge() {
  if (!ctx?.els) return false;
  window.addEventListener("helix-composer-submit", (event) => {
    const { kind, content } = event.detail || {};
    if (!content || !kind) return;
    if (kind === "operator") void sendOperatorMessage(content);
    // customer sends stay in app.js (same-named listener registered there).
  });
  window.addEventListener("helix-composer-typing", (event) => {
    const { kind, content } = event.detail || {};
    if (kind !== "operator" || typeof content !== "string") return;
    const conversationId = ctx.state.selectedId;
    if (!conversationId) return;
    // ROADMAP H02: an in-flight rewrite computed from the previous text must
    // not overwrite what the operator has typed since.
    bumpDraftVersion(conversationId);
    ctx.els.operatorInput.value = content;
    window.clearTimeout(ctx.state.draftTimer);
    if (content.trim()) {
      ctx.state.draftTimer = window.setTimeout(() => saveDraft(conversationId, content), 300);
    } else {
      clearDraft(conversationId);
    }
  });
  // Copilot tools: the island passes its own textarea content where the
  // legacy flow would have read #operatorInput.
  window.addEventListener("helix-composer-copilot-suggest", (event) => {
    const { draft } = event.detail || {};
    void fetchCopilotSuggestions({ draft });
  });
  window.addEventListener("helix-composer-copilot-tone", (event) => {
    const { tone, text } = event.detail || {};
    if (tone) void applyCopilotTone(tone, { text });
  });
  // Macro usage tracking for canned chips / macro suggestions applied
  // island-side (the insertion itself is island-local).
  window.addEventListener("helix-composer-macro-use", (event) => {
    const { macroId } = event.detail || {};
    if (macroId) {
      // ROADMAP H02: the island inserted the macro into its own textarea, so
      // an in-flight rewrite computed from the previous text is now stale.
      const conversationId = ctx.state.selectedId;
      if (conversationId) bumpDraftVersion(conversationId);
      void recordMacroUse(macroId);
    }
  });
  // Pending attachments: the island's file input hands the raw File across.
  window.addEventListener("helix-composer-attachment-upload", (event) => {
    const { file } = event.detail || {};
    if (file) void uploadPendingAttachment(file);
  });
  window.addEventListener("helix-composer-attachment-remove", (event) => {
    const { id } = event.detail || {};
    if (id) removePendingAttachment(ctx.state.selectedId, id);
  });
  // ROADMAP H02 (2.19.0): the retry re-uploads the retained File with the same
  // attempt key, so a lost response cannot store the bytes twice.
  window.addEventListener("helix-composer-attachment-retry", (event) => {
    const { token } = event.detail || {};
    const conversationId = ctx.state.selectedId;
    if (token && conversationId) void retryFailedAttachment(conversationId, token);
  });
  window.addEventListener("helix-composer-attachment-dismiss", (event) => {
    const { token } = event.detail || {};
    const conversationId = ctx.state.selectedId;
    if (token && conversationId) dismissFailedAttachment(conversationId, token);
  });
  // Browser dual-track: the legacy #operatorInput typing listener is the
  // exact counterpart of the helix-composer-typing bridge above (draft
  // autosave debounce + trailing-/ macro suggest), so both live here.
  ctx.els.operatorInput?.addEventListener("input", () => {
    const conversationId = ctx.state.selectedId;
    if (conversationId) {
      // ROADMAP H02: every keystroke advances the draft generation, so a
      // rewrite or suggestion computed from the older text is recognisable.
      bumpDraftVersion(conversationId);
      window.clearTimeout(ctx.state.draftTimer);
      ctx.state.draftTimer = window.setTimeout(() => {
        saveDraft(conversationId, ctx.els.operatorInput.value);
      }, 300);
    }
    const match = ctx.els.operatorInput.value.match(/(^|\s)\/([^\s]*)$/);
    if (match) renderMacroSuggest(match[2] || "");
    else hideMacroSuggest();
  });
  ctx.els.operatorInput?.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      ctx.els.operatorForm.requestSubmit();
      return;
    }
    if (event.key === "Escape" && ctx.state.macroOpen) {
      event.preventDefault();
      hideMacroSuggest();
    }
  });
  window.addEventListener("helix-composer-sync", () => publishComposerState());
  window.addEventListener("helix-queue-select", () => publishComposerState());
  return true;
}

export default { configure, publishComposerState, bindIslandBridge };
