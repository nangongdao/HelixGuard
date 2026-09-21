/**
 * Helix Guard — copilot tool surfaces (ROADMAP H02).
 *
 * The AI copilot bar's three write cores — reply suggestions, knowledge
 * lookups and tone rewrites — extracted from composer.js so both the legacy
 * DOM flow and the React island publish path share one implementation with
 * room under the 400-line frontend gate.
 *
 * Every one of them is a command against a *specific* conversation and a
 * *specific* draft, and every one of them used to apply its result
 * unconditionally: a slow suggestion for A painted into B's copilot bar, and a
 * rewrite computed from an older draft overwrote the text the operator had
 * typed since. Each now opens a ticket through `composer-command.js` and
 * re-checks it at apply time — `isCurrent` for the conversation/generation
 * half, `isDraftUnchanged` for the draft half.
 */

import {
  beginCommand,
  isCurrent,
  isDraftUnchanged,
} from "./composer-command.js?v=1.4.0";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/canOperate/escapeHtml). */
export function configure(deps) {
  ctx = deps;
}

/** Event the composer island listens on for copilot tool state. */
export const COMPOSER_COPILOT_EVENT = "helix-composer-copilot";

/** Publish a copilot section {status?, suggestions?, knowledge?, rewritten?}
 * to the composer island (no-op outside island mode). */
export function publishCopilot(detail) {
  if (typeof window === "undefined" || !window.__HELIX_ISLAND_MODE__) return;
  window.dispatchEvent(new CustomEvent(COMPOSER_COPILOT_EVENT, { detail }));
}

export function setCopilotStatus(text, autoHide = true) {
  if (!ctx.els.copilotStatus) return;
  ctx.els.copilotStatus.textContent = text;
  ctx.els.copilotStatus.hidden = !text;
  if (autoHide && text) {
    window.setTimeout(() => {
      if (ctx.els.copilotStatus) ctx.els.copilotStatus.hidden = true;
    }, 2500);
  }
}

let copilotSuggestionTexts = [];

export function suggestionAt(index) {
  return copilotSuggestionTexts[Number(index)];
}

/** A rewrite is only applied while the draft it was computed from is still on
 *  screen; this is the status shown when the operator has moved on. */
const STALE_REWRITE_STATUS = "改写已放弃（草稿已更新）";

export async function fetchCopilotSuggestions({ draft } = {}) {
  const conversationId = ctx.state.selectedId;
  if (!conversationId || !ctx.canOperate()) return;
  const ticket = beginCommand("copilot-suggest", conversationId);
  setCopilotStatus("生成中…", false);
  publishCopilot({ status: "生成中…" });
  try {
    const payload = await ctx.api("/api/copilot/suggest", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: conversationId,
        // Island mode passes the island textarea content; legacy reads its own input.
        draft: draft !== undefined ? draft : ctx.els.operatorInput?.value || null,
      }),
    });
    // A suggestion for A must never paint into B.
    if (!isCurrent(ticket)) return;
    const items = payload.suggestions || [];
    const mapped = items.map((item) => ({ content: item.content, source: item.source }));
    copilotSuggestionTexts = mapped.map((item) => item.content);
    if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
      publishCopilot({ suggestions: mapped, status: mapped.length ? "" : "暂无建议" });
      return;
    }
    if (!ctx.els.copilotSuggestions) return;
    if (!items.length) {
      ctx.els.copilotSuggestions.hidden = true;
      ctx.els.copilotSuggestions.innerHTML = "";
      setCopilotStatus("暂无建议");
      return;
    }
    ctx.els.copilotSuggestions.innerHTML = items
      .map((item, index) => {
        const badge = item.source === "model" ? "AI" : "模板";
        return `<button type="button" class="copilot-suggestion" data-index="${index}" title="${ctx.escapeHtml(item.content)}">`
          + `<span class="copilot-suggestion-badge">${badge}</span>`
          + `<span class="copilot-suggestion-text">${ctx.escapeHtml(item.content)}</span></button>`;
      })
      .join("");
    ctx.els.copilotSuggestions.hidden = false;
    setCopilotStatus("");
  } catch {
    if (!isCurrent(ticket)) return;
    setCopilotStatus("建议生成失败");
    publishCopilot({ status: "建议生成失败" });
  }
}

export async function loadCopilotKnowledge() {
  const conversationId = ctx.state.selectedId;
  if (!conversationId || !ctx.canOperate()) return;
  if (ctx.state.lastCopilotConv === conversationId) return;
  const ticket = beginCommand("copilot-knowledge", conversationId);
  try {
    const payload = await ctx.api("/api/copilot/policy", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId }),
    });
    // Only claim the conversation once the articles are actually applied: a
    // result discarded by a switch must leave the cache cold, so returning to
    // the conversation reloads instead of showing nothing.
    if (!isCurrent(ticket)) return;
    ctx.state.lastCopilotConv = conversationId;
    const articles = (payload.articles || []).map((article) => ({
      title: article.title,
      category: article.category || "",
    }));
    if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
      publishCopilot({ knowledge: articles });
      return;
    }
    if (!ctx.els.copilotKnowledge) return;
    ctx.els.copilotKnowledge.hidden = !articles.length;
    ctx.els.copilotKnowledge.innerHTML = articles
      .map((article) => `<button type="button" class="copilot-kb-item" data-title="${ctx.escapeHtml(article.title)}">`
        + `<span class="copilot-kb-title">${ctx.escapeHtml(article.title)}</span>`
        + `<span class="copilot-kb-cat">${ctx.escapeHtml(article.category)}</span></button>`)
      .join("");
  } catch {
    if (!isCurrent(ticket)) return;
    ctx.state.lastCopilotConv = conversationId;
    if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
      publishCopilot({ knowledge: [] });
      return;
    }
    if (ctx.els.copilotKnowledge) ctx.els.copilotKnowledge.hidden = true;
  }
}

export async function applyCopilotTone(tone, { text } = {}) {
  const conversationId = ctx.state.selectedId;
  const value = text !== undefined ? text : ctx.els.operatorInput?.value || "";
  if (!value.trim()) {
    setCopilotStatus("先输入草稿再改写");
    publishCopilot({ status: "先输入草稿再改写" });
    return;
  }
  const ticket = beginCommand("copilot-tone", conversationId);
  setCopilotStatus("改写中…", false);
  publishCopilot({ status: "改写中…" });
  try {
    const payload = await ctx.api("/api/copilot/rewrite", {
      method: "POST",
      body: JSON.stringify({ text: value, tone }),
    });
    // Reject both the wrong conversation (switch during the rewrite) and the
    // stale draft (the operator kept typing): their newer text wins.
    if (!isDraftUnchanged(ticket)) {
      if (isCurrent(ticket)) {
        setCopilotStatus(STALE_REWRITE_STATUS);
        publishCopilot({ status: STALE_REWRITE_STATUS });
      }
      return;
    }
    const statusText = payload.source === "model" ? "已改写" : "原样保留（模型不可用）";
    if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
      publishCopilot({ status: statusText, rewritten: payload.rewritten });
      return;
    }
    ctx.els.operatorInput.value = payload.rewritten;
    setCopilotStatus(statusText);
  } catch {
    if (!isCurrent(ticket)) return;
    setCopilotStatus("改写失败");
    publishCopilot({ status: "改写失败" });
  }
}

export function resetCopilot() {
  ctx.state.lastCopilotConv = null;
  copilotSuggestionTexts = [];
  if (ctx.els.copilotSuggestions) {
    ctx.els.copilotSuggestions.hidden = true;
    ctx.els.copilotSuggestions.innerHTML = "";
  }
  if (ctx.els.copilotKnowledge) {
    ctx.els.copilotKnowledge.hidden = true;
    ctx.els.copilotKnowledge.innerHTML = "";
  }
  if (ctx.els.copilotStatus) ctx.els.copilotStatus.hidden = true;
}

export default {
  configure,
  COMPOSER_COPILOT_EVENT,
  publishCopilot,
  setCopilotStatus,
  suggestionAt,
  fetchCopilotSuggestions,
  loadCopilotKnowledge,
  applyCopilotTone,
  resetCopilot,
};
