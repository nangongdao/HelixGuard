/**
 * Helix Guard — composer domain React island (D3 + tools slice)
 *
 * Mirrors the message composer forms AND the operator tool surfaces in the
 * desktop shell. Legacy composer.js owns the send lifecycle (drafts, macros,
 * copilot, shortcuts); the island is the renderer once the legacy forms are
 * yielded. It uses the legacy element ids/aria contract so accessible
 * locators keep working, bridging every interaction back to legacy:
 *   helix-composer-submit             {kind, content}   → legacy send
 *   helix-composer-typing             {kind, content}   → legacy draft/macro hinting
 *   helix-composer-copilot-suggest    {draft}           → fetchCopilotSuggestions
 *   helix-composer-copilot-tone       {tone, text}      → applyCopilotTone
 *   helix-composer-macro-use          {macroId}         → usage tracking
 *   helix-composer-attachment-upload  {file}            → uploadPendingAttachment
 *   helix-composer-attachment-remove  {id}              → pending removal
 *   helix-composer-attachment-retry   {token}           → retry a failed upload
 *   helix-composer-attachment-dismiss {token}           → discard a failed upload
 * State flows the other way via helix-composer-state snapshots (resolved,
 * human, busy flags, drafts, cannedResponses, pendingAttachments,
 * failedAttachments, canOperate) and helix-composer-copilot tool events
 * published by composer.js.
 *
 * The mount point stays hidden in a plain browser tab (legacy is the renderer
 * there); the desktop shell unhides it and yields #submitterForm/#reviewerForm.
 *
 * See DESKTOP_TAURI_PLAN.md §D3.
 */

import React, { useState, useCallback, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";

import { COMPOSER_EVENTS, INPUT_IDS, macroMatches } from "./composer/constants.js";
import { AttachmentBar, CannedBar, CopilotBar, MacroSuggest } from "./composer/tool-bars.jsx";

export { COMPOSER_EVENTS, INPUT_IDS, macroMatches } from "./composer/constants.js";

export function mount(element) {
  const root = createRoot(element);
  root.render(<ComposerIsland />);
}

export function ComposerIsland() {
  const [state, setState] = useState({
    resolved: false,
    human: false,
    customerBusy: false,
    operatorBusy: false,
    customerDraft: "",
    operatorDraft: "",
    operatorDraftRevision: 0,
    canOperate: false,
    cannedResponses: [],
    pendingAttachments: [],
    failedAttachments: [],
  });
  const [customerMessage, setCustomerMessage] = useState("");
  const [operatorReply, setOperatorReply] = useState("");
  const [tone, setTone] = useState("");
  const [copilot, setCopilot] = useState({ status: "", suggestions: [], policy: [] });
  const lastStateRef = useRef(state);
  const syncedDraftRef = useRef({ text: null, revision: null });
  const operatorInputRef = useRef(null);

  useEffect(() => {
    const onState = (event) => {
      const next = { ...lastStateRef.current, ...(event.detail || {}) };
      lastStateRef.current = next;
      setState(next);
    };
    const onCopilot = (event) => {
      const detail = event.detail || {};
      setCopilot((prev) => ({
        status: detail.status !== undefined ? detail.status : prev.status,
        suggestions: detail.suggestions !== undefined ? detail.suggestions : prev.suggestions,
        policy: detail.policy !== undefined ? detail.policy : prev.policy,
      }));
      // Tone rewrite results land directly in the mirrored textarea.
      if (detail.rewritten !== undefined) setOperatorReply(detail.rewritten);
    };
    window.addEventListener(COMPOSER_EVENTS.STATE, onState);
    window.addEventListener(COMPOSER_EVENTS.COPILOT, onCopilot);
    // Ask legacy for the current composer state on mount (islands mount after
    // the legacy first render).
    window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.SYNC));
    return () => {
      window.removeEventListener(COMPOSER_EVENTS.STATE, onState);
      window.removeEventListener(COMPOSER_EVENTS.COPILOT, onCopilot);
    };
  }, []);

  // Keep the mirrored textarea values in sync when legacy publishes a draft
  // (e.g. switching conversation loads a saved draft).
  useEffect(() => {
    setCustomerMessage((prev) => (state.customerDraft !== undefined ? state.customerDraft : prev));
  }, [state.customerDraft]);
  // ROADMAP H02 (2.19.0): re-sync when the published text differs *or* when
  // legacy's draft generation advanced. The generation is what makes a
  // confirmed send observable: it clears the box to "" without changing the
  // value when the reply was the first thing typed, so a value-only comparison
  // would leave the sent text sitting in the mirror.
  useEffect(() => {
    if (state.operatorDraft === undefined) return;
    const revision = state.operatorDraftRevision ?? 0;
    const synced = syncedDraftRef.current;
    if (state.operatorDraft === synced.text && revision === synced.revision) return;
    syncedDraftRef.current = { text: state.operatorDraft, revision };
    setOperatorReply(state.operatorDraft);
  }, [state.operatorDraft, state.operatorDraftRevision]);

  const emitTyping = useCallback((kind, content) => {
    window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.TYPING, { detail: { kind, content } }));
  }, []);

  const emitSubmit = useCallback((kind, content) => {
    if (!content.trim()) return;
    window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.SUBMIT, { detail: { kind, content } }));
  }, []);

  const handleCustomerSubmit = useCallback(
    (e) => {
      e.preventDefault();
      if (state.resolved || state.customerBusy) return;
      const content = customerMessage.trim();
      if (!content) return;
      emitSubmit("customer", content);
      setCustomerMessage("");
    },
    [customerMessage, state.resolved, state.customerBusy, emitSubmit],
  );

  const handleOperatorSubmit = useCallback(
    (e) => {
      e.preventDefault();
      if (state.operatorBusy) return;
      const content = operatorReply.trim();
      if (!content) return;
      emitSubmit("operator", content);
      // ROADMAP H02: deliberately *not* cleared here. The legacy composer
      // owns the send lifecycle and only clears on a confirmed send, so
      // clearing on submit would empty the box the operator is looking at
      // while the text they sent is still unconfirmed — a failed send would
      // silently look like a lost draft. The confirmed send republishes the
      // composer state, which syncs this textarea in the effect below.
    },
    [operatorReply, state.operatorBusy, emitSubmit],
  );

  // ── Operator tool surfaces (human + canOperate gated, like legacy) ──
  const toolsVisible = state.human && state.canOperate;

  // Macro suggest: legacy renderMacroSuggest mirrors — match a trailing
  // /token against the canned catalog.
  const tokenMatch = operatorReply.match(/(^|\s)\/([^\s]*)$/);
  const macroOptions = toolsVisible && tokenMatch ? macroMatches(state.cannedResponses, tokenMatch[2]) : [];

  const handleMacroPick = useCallback(
    (macro, viaToken) => {
      setOperatorReply((prev) => {
        const match = prev.match(/(^|\s)\/([^\s]*)$/);
        if (viaToken && match) {
          const start = prev.slice(0, prev.length - match[0].length + (match[1] ? match[1].length : 0));
          return `${start}${macro.body}`;
        }
        return macro.body;
      });
      window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.MACRO_USE, { detail: { macroId: macro.id } }));
      operatorInputRef.current?.focus();
    },
    [],
  );

  const handleCannedChip = useCallback((macro) => {
    setOperatorReply((prev) => {
      const prefix = prev.trim();
      return prefix ? `${prefix}\n${macro.body}` : macro.body;
    });
    window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.MACRO_USE, { detail: { macroId: macro.id } }));
    operatorInputRef.current?.focus();
  }, []);

  const handleSuggest = useCallback(() => {
    window.dispatchEvent(
      new CustomEvent(COMPOSER_EVENTS.COPILOT_SUGGEST, { detail: { draft: operatorReply } }),
    );
  }, [operatorReply]);

  const handleTone = useCallback(
    (event) => {
      const selected = event.target.value;
      event.target.value = "";
      setTone("");
      if (selected) {
        window.dispatchEvent(
          new CustomEvent(COMPOSER_EVENTS.COPILOT_TONE, { detail: { tone: selected, text: operatorReply } }),
        );
      }
    },
    [operatorReply],
  );

  const applySuggestion = useCallback(
    (content) => {
      setOperatorReply(content);
      // ROADMAP H02: the draft changed through a tool rather than a keystroke,
      // so announce it — an in-flight tone rewrite computed from the previous
      // text must not overwrite the suggestion the operator just applied.
      emitTyping("operator", content);
      operatorInputRef.current?.focus();
    },
    [emitTyping],
  );

  const handleFileChange = useCallback((event) => {
    const file = event.target.files?.[0];
    if (file) {
      window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.ATTACHMENT_UPLOAD, { detail: { file } }));
    }
    event.target.value = "";
  }, []);

  return (
    <div className="composer-island">
      <form
        id={INPUT_IDS.submitterForm}
        className="composer submitter-composer"
        onSubmit={handleCustomerSubmit}
        data-busy={String(state.customerBusy)}
        aria-busy={state.customerBusy}
      >
        <label className="sr-only" htmlFor={INPUT_IDS.submitterInput}>待审内容</label>
        <textarea
          id={INPUT_IDS.submitterInput}
          rows={2}
          maxLength={4000}
          placeholder="输入一条模拟待审内容…"
          required
          disabled={state.resolved || state.customerBusy}
          value={customerMessage}
          onChange={(e) => {
            setCustomerMessage(e.target.value);
            emitTyping("customer", e.target.value);
          }}
        />
        <button
          className="send-button"
          type="submit"
          title="发送待审内容"
          aria-label="发送待审内容"
          disabled={state.resolved || state.customerBusy}
        >
          <svg className="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#send" /></svg>
        </button>
      </form>
      <CannedBar
        toolsVisible={toolsVisible}
        cannedResponses={state.cannedResponses}
        onPick={handleCannedChip}
      />
      <form
        id={INPUT_IDS.reviewerForm}
        className="composer reviewer-composer"
        onSubmit={handleOperatorSubmit}
        data-busy={String(state.operatorBusy)}
        aria-busy={state.operatorBusy}
        hidden={!state.human}
      >
        <label className="sr-only" htmlFor={INPUT_IDS.reviewerInput}>人工回复</label>
        <div className="composer-stack">
          <textarea
            id={INPUT_IDS.reviewerInput}
            ref={operatorInputRef}
            rows={2}
            maxLength={4000}
            placeholder="输入人工回复… 输入 / 插入预置结论，Ctrl+Enter 发送"
            disabled={state.operatorBusy}
            value={operatorReply}
            onChange={(e) => {
              setOperatorReply(e.target.value);
              emitTyping("operator", e.target.value);
            }}
          />
          <MacroSuggest options={macroOptions} onPick={handleMacroPick} />
          <CopilotBar
            toolsVisible={toolsVisible}
            copilot={copilot}
            tone={tone}
            onSuggest={handleSuggest}
            onTone={handleTone}
            onApply={applySuggestion}
          />
          <AttachmentBar
            toolsVisible={toolsVisible}
            pendingAttachments={state.pendingAttachments}
            failedAttachments={state.failedAttachments}
            onFileChange={handleFileChange}
          />
          <div className="composer-actions">
            <button
              className="send-button reviewer-send"
              type="submit"
              title="发送人工回复"
              aria-label="发送人工回复"
              disabled={state.operatorBusy}
            >
              <svg className="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#send" /></svg>
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

export default { mount, ComposerIsland };
