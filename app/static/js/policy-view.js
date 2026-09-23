/**
 * Helix Guard — policy view lifecycle (D3 long tail slice 17).
 *
 * The policy page's DOM lifecycle (summary/filter/list, cached fetch,
 * editor + review flows, listeners and island bridges); app.js keeps a thin
 * loadKnowledgeView wrapper. Browser tab → legacy paint; desktop shell → the
 * policy island owns the surface, writes stay here.
 */

import {
  KNOWLEDGE_STATUS_LABELS, knowledgeFormPayload, normalizeKnowledgeArticle,
  filterKnowledgeArticles, summarizeKnowledgeArticles, reviewActionsFor,
} from "./policy.js?v=1.4.0";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

export const KNOWLEDGE_EVENTS = Object.freeze({
  REFRESH: "helix-knowledge-refresh", NEW: "helix-knowledge-new", SAVE: "helix-knowledge-save",
  SAVED: "helix-knowledge-saved", ACTION: "helix-knowledge-action",
});

export function canWriteKnowledge() {
  // `knowledge:write` is a wire-contract permission (app/security.py) — not migrated.
  return ctx.state.me?.permissions?.includes("knowledge:write") === true;
}
export function syncKnowledgeLanguageSelects() {
  // Selects must cover every languageNames entry or unlisted-language
  // articles silently lose their language on edit (audit: backlog 1).
  for (const select of [ctx.els.policyLanguage, ctx.els.policyLanguageFilter]) {
    if (!select) continue;
    const covered = new Set([...select.options].map((option) => option.value));
    const missing = Object.keys(ctx.languageNames)
      .sort((a, b) => ctx.languageNames[a].localeCompare(ctx.languageNames[b], "zh"))
      .filter((code) => !covered.has(code));
    for (const code of missing) {
      select.insertAdjacentHTML("beforeend", `<option value="${code}">${ctx.languageNames[code]}</option>`);
    }
  }
}

export function knowledgeFilters() {
  return {
    query: ctx.els.policySearch?.value || "",
    status: ctx.els.policyStatusFilter?.value || "all",
    language: ctx.els.policyLanguageFilter?.value || "",
  };
}

export function renderKnowledgeSummary() {
  if (!ctx.els.policySummary) return;
  const summary = summarizeKnowledgeArticles(ctx.state.knowledgeArticles);
  const writer = canWriteKnowledge();
  const rows = [
    ["全部", summary.total],
    ["已发布", summary.published],
    ["草稿", writer ? summary.draft : "—"],
    ["待审核", writer ? summary.pending_review : "—"],
    ["已停用", writer ? summary.retired : "—"],
  ];
  ctx.els.policySummary.innerHTML = rows
    .map(
      ([label, count]) =>
        `<div class="policy-summary-item"><span>${label}</span><strong>${ctx.escapeHtml(count)}</strong></div>`,
    )
    .join("");
}

export function knowledgeSourceMarkup(article) {
  const source = String(article.source_url || "");
  if (/^https?:\/\//i.test(source)) {
    return `<a class="policy-source" href="${ctx.escapeHtml(source)}" target="_blank" rel="noopener noreferrer" title="${ctx.escapeHtml(source)}">${ctx.escapeHtml(source)}</a>`;
  }
  return `<span class="policy-source" title="${ctx.escapeHtml(source)}">${ctx.escapeHtml(source || "未记录来源")}</span>`;
}

export function renderKnowledgeArticles() {
  if (!ctx.els.policyList) return;
  const articles = filterKnowledgeArticles(ctx.state.knowledgeArticles, knowledgeFilters());
  const writer = canWriteKnowledge();
  if (ctx.els.policyResultCount) {
    ctx.els.policyResultCount.textContent = `${articles.length} / ${ctx.state.knowledgeArticles.length} 篇`;
  }
  if (!articles.length) {
    ctx.els.policyList.innerHTML = "";
    if (ctx.els.policyListStatus) {
      ctx.els.policyListStatus.textContent = ctx.state.knowledgeArticles.length
        ? "没有符合当前筛选条件的文章。"
        : "当前租户还没有策略文章。";
    }
    return;
  }
  if (ctx.els.policyListStatus) ctx.els.policyListStatus.textContent = "";
  ctx.els.policyList.innerHTML = articles
    .map((raw) => {
      const article = normalizeKnowledgeArticle(raw);
      const status = Object.hasOwn(KNOWLEDGE_STATUS_LABELS, article.status)
        ? article.status
        : "retired";
      const statusLabel = KNOWLEDGE_STATUS_LABELS[status] || status;
      const language = article.language ? ctx.languageNames[article.language] || article.language : "通用";
      const tags = article.tags.length
        ? article.tags.map((tag) => `<span class="policy-tag">${ctx.escapeHtml(tag)}</span>`).join("")
        : '<span class="policy-tag">未分类</span>';
      const reviewButtons = writer
        ? reviewActionsFor(article)
            .map((action) => {
              const label = action === "publish" ? "发布" : "停用";
              return `<button type="button" class="policy-action" data-action="${action}" data-article-id="${ctx.escapeHtml(article.id)}">${label}</button>`;
            })
            .join("")
        : "";
      const editButton = writer
        ? `<button type="button" class="policy-action" data-action="edit" data-article-id="${ctx.escapeHtml(article.id)}">编辑</button>`
        : "";
      return `<article class="policy-article" role="listitem" data-article-id="${ctx.escapeHtml(article.id)}">
        <div class="policy-article-head">
          <h3>${ctx.escapeHtml(article.title)}</h3>
          <span class="policy-status ${status}">${ctx.escapeHtml(statusLabel)}</span>
        </div>
        <div class="policy-article-meta">
          <span>${ctx.escapeHtml(article.category)}</span><span>·</span>
          <span>${ctx.escapeHtml(language)}</span><span>·</span>
          <span>v${ctx.escapeHtml(article.version || 1)}</span><span>·</span>
          <time>${ctx.escapeHtml(ctx.formatTime(article.updated_at, true))}</time>
        </div>
        <div class="policy-tag-list">${tags}</div>
        <details>
          <summary>查看正文</summary>
          <p class="policy-article-content">${ctx.escapeHtml(article.content)}</p>
        </details>
        ${knowledgeSourceMarkup(article)}
        ${writer ? `<div class="policy-article-actions">${editButton}${reviewButtons}</div>` : ""}
      </article>`;
    })
    .join("");
}

export function updateKnowledgeAccessState() {
  const writer = canWriteKnowledge();
  if (ctx.els.newPolicyDraft) ctx.els.newPolicyDraft.hidden = !writer;
  // Island mode: the island owns the notice/filter/editor (no un-hiding).
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) return;
  if (ctx.els.policyReadOnly) ctx.els.policyReadOnly.hidden = writer;
  if (!writer && ctx.els.policyEditor) ctx.els.policyEditor.hidden = true;
  if (!writer && ctx.els.policyStatusFilter) {
    for (const option of ctx.els.policyStatusFilter.options) {
      option.disabled = option.value !== "all" && option.value !== "published";
    }
    if (!["all", "published"].includes(ctx.els.policyStatusFilter.value)) {
      ctx.els.policyStatusFilter.value = "published";
    }
  } else if (writer && ctx.els.policyStatusFilter) {
    for (const option of ctx.els.policyStatusFilter.options) option.disabled = false;
  }
}

export async function loadKnowledgeView({ force = false } = {}) {
  if (!ctx.els.policyView) return;
  updateKnowledgeAccessState();
  // Island mode: the island owns the fetch + DOM — hand the refresh over
  // (`force` carries the cache decision for a fresh react-query fetch).
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    window.dispatchEvent(new CustomEvent(KNOWLEDGE_EVENTS.REFRESH, { detail: { force } }));
    return;
  }
  syncKnowledgeLanguageSelects();
  // Per-permission cache: role changes never leak drafts to readers (backlog 4).
  if (
    !force &&
    ctx.state.knowledgeLoadedForWriter === canWriteKnowledge() &&
    ctx.state.knowledgeLoadedAt &&
    Date.now() - ctx.state.knowledgeLoadedAt < 15000
  ) {
    renderKnowledgeSummary();
    renderKnowledgeArticles();
    return;
  }
  if (ctx.els.policyList) ctx.els.policyList.setAttribute("aria-busy", "true");
  if (ctx.els.policyListStatus) ctx.els.policyListStatus.textContent = "正在加载文章…";
  try {
    const path = canWriteKnowledge() ? "/api/policy?include_inactive=true" : "/api/policy";
    const articles = await ctx.api(path);
    ctx.state.knowledgeArticles = Array.isArray(articles)
      ? articles.map(normalizeKnowledgeArticle)
      : [];
    ctx.state.knowledgeLoadedForWriter = canWriteKnowledge();
    ctx.state.knowledgeLoadedAt = Date.now();
    renderKnowledgeSummary();
    renderKnowledgeArticles();
  } catch (error) {
    ctx.state.knowledgeArticles = [];
    ctx.state.knowledgeLoadedForWriter = null;
    ctx.state.knowledgeLoadedAt = 0;
    if (ctx.els.policyList) ctx.els.policyList.innerHTML = "";
    if (ctx.els.policyListStatus) {
      ctx.els.policyListStatus.textContent = `策略文章加载失败：${error.message || error}`;
    }
  } finally {
    if (ctx.els.policyList) ctx.els.policyList.setAttribute("aria-busy", "false");
  }
}

export function resetKnowledgeEditor({ close = false } = {}) {
  ctx.state.knowledgeEditingId = null;
  // eslint-disable-next-line no-multi-assign
  ctx.els.policyForm?.reset();
  if (ctx.els.policyCategory) ctx.els.policyCategory.value = "general";
  if (ctx.els.policyEditorTitle) ctx.els.policyEditorTitle.textContent = "新建策略草稿";
  if (ctx.els.policySaveLabel) ctx.els.policySaveLabel.textContent = "保存草稿";
  if (ctx.els.policyEditor) ctx.els.policyEditor.hidden = close;
}

export function editKnowledgeArticle(articleId) {
  if (!canWriteKnowledge()) return;
  const raw = ctx.state.knowledgeArticles.find((article) => article.id === articleId);
  if (!raw) return;
  const article = normalizeKnowledgeArticle(raw);
  ctx.state.knowledgeEditingId = article.id;
  if (ctx.els.policyTitle) ctx.els.policyTitle.value = article.title;
  if (ctx.els.policyContent) ctx.els.policyContent.value = article.content;
  if (ctx.els.policyTags) ctx.els.policyTags.value = article.tags.join(", ");
  if (ctx.els.policyCategory) ctx.els.policyCategory.value = article.category;
  if (ctx.els.policyLanguage) ctx.els.policyLanguage.value = article.language || "";
  if (ctx.els.policySource) ctx.els.policySource.value = article.source_url;
  if (ctx.els.policyEditorTitle) ctx.els.policyEditorTitle.textContent = "编辑策略文章";
  if (ctx.els.policySaveLabel) ctx.els.policySaveLabel.textContent = "保存修改";
  if (ctx.els.policyEditor) ctx.els.policyEditor.hidden = false;
  ctx.els.policyTitle?.focus({ preventScroll: true });
}

export async function saveKnowledgeArticle(event) {
  event.preventDefault();
  if (!canWriteKnowledge() || !ctx.els.policyForm) return;
  const payload = knowledgeFormPayload({
    title: ctx.els.policyTitle?.value,
    content: ctx.els.policyContent?.value,
    tags: ctx.els.policyTags?.value,
    category: ctx.els.policyCategory?.value,
    sourceUrl: ctx.els.policySource?.value,
    language: ctx.els.policyLanguage?.value,
  });
  if (!payload.tags.length) {
    ctx.showToast("请至少填写一个策略标签", true);
    ctx.els.policyTags?.focus();
    return;
  }
  // minlength counts raw chars; a trimmed payload can still miss the backend
  // minimums (2 title / 10 content) — surface it before the 422.
  if (payload.title.length < 2) {
    ctx.showToast("标题至少需要 2 个字符", true);
    ctx.els.policyTitle?.focus();
    return;
  }
  if (payload.content.length < 10) {
    ctx.showToast("正文至少需要 10 个字符", true);
    ctx.els.policyContent?.focus();
    return;
  }
  const editingId = ctx.state.knowledgeEditingId;
  ctx.setFormBusy(ctx.els.policyForm, true);
  try {
    const path = editingId
      ? `/api/policy/${encodeURIComponent(editingId)}`
      : "/api/policy/drafts";
    await ctx.api(path, {
      method: editingId ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    });
    resetKnowledgeEditor({ close: true });
    ctx.state.knowledgeLoadedAt = 0;
    await loadKnowledgeView({ force: true });
    ctx.showToast(editingId ? "策略文章已更新" : "策略草稿已创建");
  } catch (error) {
    ctx.showToast(`策略文章保存失败：${error.message || error}`, true);
  } finally {
    ctx.setFormBusy(ctx.els.policyForm, false);
  }
}

// Bridge: the island validates before dispatching; this is the api()/toast half.
export async function saveKnowledgeFromIsland({ payload, editingId } = {}) {
  if (!canWriteKnowledge() || !payload) return;
  let ok = false;
  try {
    const path = editingId
      ? `/api/policy/${encodeURIComponent(editingId)}`
      : "/api/policy/drafts";
    await ctx.api(path, { method: editingId ? "PATCH" : "POST", body: JSON.stringify(payload) });
    ok = true;
    ctx.state.knowledgeLoadedAt = 0;
    ctx.showToast(editingId ? "策略文章已更新" : "策略草稿已创建");
  } catch (error) {
    ctx.showToast(`策略文章保存失败：${error.message || error}`, true);
  } finally {
    window.dispatchEvent(new CustomEvent(KNOWLEDGE_EVENTS.SAVED, { detail: { ok } }));
  }
}

export async function reviewKnowledgeArticle(articleId, action) {
  if (!canWriteKnowledge() || !["publish", "retire"].includes(action)) return;
  if (action === "retire" && !window.confirm("确认停用该策略文章？停用后将不再参与检索。")) {
    return;
  }
  if (ctx.els.policyList) ctx.els.policyList.setAttribute("aria-busy", "true");
  try {
    await ctx.api(`/api/policy/${encodeURIComponent(articleId)}/review`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    ctx.state.knowledgeLoadedAt = 0;
    await loadKnowledgeView({ force: true });
    ctx.showToast(action === "publish" ? "策略文章已发布" : "策略文章已停用");
  } catch (error) {
    ctx.showToast(`审核操作失败：${error.message || error}`, true);
  } finally {
    if (ctx.els.policyList) ctx.els.policyList.setAttribute("aria-busy", "false");
  }
}

/**
 * Bind the policy page listeners and island bridges (exactly once, at
 * app.js load). The legacy listeners only reach their DOM in a plain browser
 * tab; the island bridges carry the desktop shell's writes.
 */
export function bindKnowledgeView() {
  if (!ctx?.els) return false;
  const islandMode = () => typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__;
  if (ctx.els.policySearch) {
    ctx.els.policySearch.addEventListener("input", () => renderKnowledgeArticles());
  }
  if (ctx.els.policyStatusFilter) {
    ctx.els.policyStatusFilter.addEventListener("change", () => renderKnowledgeArticles());
  }
  if (ctx.els.policyLanguageFilter) {
    ctx.els.policyLanguageFilter.addEventListener("change", () => renderKnowledgeArticles());
  }
  if (ctx.els.newPolicyDraft) {
    ctx.els.newPolicyDraft.addEventListener("click", () => {
      // The header button is outside the island mount — island mode opens the
      // island's editor instead of the hidden form.
      if (islandMode()) {
        window.dispatchEvent(new CustomEvent(KNOWLEDGE_EVENTS.NEW));
        return;
      }
      resetKnowledgeEditor();
      ctx.els.policyTitle?.focus({ preventScroll: true });
    });
  }
  if (ctx.els.refreshPolicy) {
    ctx.els.refreshPolicy.addEventListener("click", () => {
      ctx.state.knowledgeLoadedAt = 0;
      void loadKnowledgeView({ force: true });
    });
  }
  if (ctx.els.cancelPolicyEdit) {
    ctx.els.cancelPolicyEdit.addEventListener("click", () => resetKnowledgeEditor({ close: true }));
  }
  if (ctx.els.resetPolicyForm) {
    ctx.els.resetPolicyForm.addEventListener("click", () => resetKnowledgeEditor());
  }
  if (ctx.els.policyForm) {
    ctx.els.policyForm.addEventListener("submit", (event) => void saveKnowledgeArticle(event));
  }
  if (ctx.els.policyList) {
    ctx.els.policyList.addEventListener("click", (event) => {
      const button = event.target.closest(".policy-action");
      if (!button) return;
      const articleId = button.dataset.articleId;
      if (button.dataset.action === "edit") editKnowledgeArticle(articleId);
      else void reviewKnowledgeArticle(articleId, button.dataset.action);
    });
  }
  if (typeof window !== "undefined") {
    // Island bridges: the policy island owns the surface, the write
    // lifecycle stays here (reviewKnowledgeArticle owns the retire confirm).
    window.addEventListener(KNOWLEDGE_EVENTS.ACTION, (event) => {
      const { action, articleId } = event.detail || {};
      if (!articleId || !["publish", "retire"].includes(action)) return;
      void reviewKnowledgeArticle(articleId, action);
    });
    window.addEventListener(KNOWLEDGE_EVENTS.SAVE, (event) => {
      void saveKnowledgeFromIsland(event.detail || {});
    });
  }
  return true;
}

export default {
  KNOWLEDGE_EVENTS, canWriteKnowledge, syncKnowledgeLanguageSelects, knowledgeFilters,
  renderKnowledgeSummary, knowledgeSourceMarkup, renderKnowledgeArticles,
  updateKnowledgeAccessState, loadKnowledgeView, resetKnowledgeEditor,
  editKnowledgeArticle, saveKnowledgeArticle, saveKnowledgeFromIsland,
  reviewKnowledgeArticle, bindKnowledgeView,
};
