/**
 * Helix Guard — policy island presentational components.
 *
 * Summary counters, the article card and the draft editor. Split out of
 * policy-island.jsx (400-line module limit); the island root keeps the
 * data/query lifecycle and the legacy write bridge.
 *
 * The legacy DOM id + class contract (policyEditor/policyTitle/…) is
 * preserved so the ui_policy and axe keyboard-path locators resolve.
 */

import React, { useEffect, useRef } from "react";

import {
  EDITOR_IDS,
  KNOWLEDGE_STATUS_LABELS,
  LANGUAGE_CODES,
  LANGUAGE_NAMES,
  reviewActionsFor,
} from "./domain.js";

export function KnowledgeSummary({ summary, canWrite }) {
  const rows = [
    ["全部", summary.total],
    ["已发布", summary.published],
    ["草稿", canWrite ? summary.draft : "—"],
    ["待审核", canWrite ? summary.pending_review : "—"],
    ["已停用", canWrite ? summary.retired : "—"],
  ];
  return (
    <div className="policy-summary" aria-live="polite">
      {rows.map(([label, count]) => (
        <div className="policy-summary-item" key={label}>
          <span>{label}</span>
          <strong>{String(count)}</strong>
        </div>
      ))}
    </div>
  );
}

export function KnowledgeArticle({ article, canWrite, onAction }) {
  const status = Object.hasOwn(KNOWLEDGE_STATUS_LABELS, article.status) ? article.status : "retired";
  const statusLabel = KNOWLEDGE_STATUS_LABELS[status] || status;
  const language = article.language ? LANGUAGE_NAMES[article.language] || article.language : "通用";
  const tags = article.tags.length
    ? article.tags
    : ["未分类"];
  const reviewActions = canWrite ? reviewActionsFor(article) : [];
  const isUrl = /^https?:\/\//i.test(article.source_url);

  return (
    <article className="policy-article" role="listitem" data-article-id={article.id}>
      <div className="policy-article-head">
        <h3>{article.title}</h3>
        <span className={`policy-status ${status}`}>{statusLabel}</span>
      </div>
      <div className="policy-article-meta">
        <span>{article.category}</span><span>·</span>
        <span>{language}</span><span>·</span>
        <span>v{article.version || 1}</span><span>·</span>
        <time>{article.updated_at || ""}</time>
      </div>
      <div className="policy-tag-list">
        {tags.map((tag) => (
          <span className="policy-tag" key={tag}>{tag}</span>
        ))}
      </div>
      <details>
        <summary>查看正文</summary>
        <p className="policy-article-content">{article.content}</p>
      </details>
      {isUrl ? (
        <a className="policy-source" href={article.source_url} target="_blank" rel="noopener noreferrer" title={article.source_url}>
          {article.source_url}
        </a>
      ) : (
        <span className="policy-source" title={article.source_url}>
          {article.source_url || "未记录来源"}
        </span>
      )}
      {canWrite && (
        <div className="policy-article-actions">
          <button type="button" className="policy-action" data-action="edit" data-article-id={article.id} onClick={() => onAction("edit", article.id)}>
            编辑
          </button>
          {reviewActions.map((action) => (
            <button key={action} type="button" className="policy-action" data-action={action} data-article-id={article.id} onClick={() => onAction(action, article.id)}>
              {action === "publish" ? "发布" : "停用"}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}

/**
 * The aside stays in the DOM even when closed: the single-column layout rule
 * is `.policy-layout:has(.policy-editor[hidden])`, so dropping the
 * element would leave the list squeezed beside a blank 420px column. The form
 * itself only mounts while open, which also gives the title its focus-on-open
 * for free (legacy editKnowledgeArticle/newPolicyDraft both focus it).
 */
export function KnowledgeEditor({ editor, dispatch, onSubmit }) {
  return (
    <aside className="policy-editor" hidden={!editor} aria-label="策略草稿编辑器">
      {editor && <KnowledgeEditorForm editor={editor} dispatch={dispatch} onSubmit={onSubmit} />}
    </aside>
  );
}

function KnowledgeEditorForm({ editor, dispatch, onSubmit }) {
  const titleRef = useRef(null);
  const editing = Boolean(editor.articleId);

  useEffect(() => {
    titleRef.current?.focus({ preventScroll: true });
  }, []);

  const field = (name) => ({
    value: editor.values[name],
    onChange: (e) => dispatch({ type: "SET_FIELD", name, value: e.target.value }),
  });

  // Keep an unmapped language (ru/ar/… beyond the list, or a code the backend
  // added later) selectable so editing never silently drops it.
  const current = editor.values.language;
  const codes = current && !LANGUAGE_CODES.includes(current) ? [...LANGUAGE_CODES, current] : LANGUAGE_CODES;

  return (
    <form id={EDITOR_IDS.form} onSubmit={onSubmit} data-busy={String(editor.busy)} aria-busy={editor.busy}>
      <header className="policy-editor-heading">
        <div>
          <span className="section-kicker">EDITOR</span>
          <h3 id={EDITOR_IDS.heading}>{editing ? "编辑策略文章" : "新建策略草稿"}</h3>
        </div>
        <button
          className="icon-button"
          type="button"
          title="关闭编辑器"
          aria-label="关闭编辑器"
          onClick={() => dispatch({ type: "CLOSE_EDITOR" })}
        >
          <svg className="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#x" /></svg>
        </button>
      </header>
      {editor.error && (
        <p className="policy-editor-error" role="alert">{editor.error.message}</p>
      )}
      <label className="policy-field">标题
        <input id={EDITOR_IDS.title} ref={titleRef} type="text" minLength={2} maxLength={160} required {...field("title")} />
      </label>
      <label className="policy-field">正文
        <textarea id={EDITOR_IDS.content} rows={10} minLength={10} maxLength={12000} required {...field("content")} />
      </label>
      <div className="policy-form-grid">
        <label className="policy-field">标签
          <input id={EDITOR_IDS.tags} type="text" maxLength={820} placeholder="shipping, refund" required {...field("tags")} />
        </label>
        <label className="policy-field">分类
          <input id={EDITOR_IDS.category} type="text" minLength={2} maxLength={60} required {...field("category")} />
        </label>
        <label className="policy-field">语言
          <select id={EDITOR_IDS.language} {...field("language")}>
            <option value="">不限语言</option>
            {codes.map((code) => (
              <option value={code} key={code}>{LANGUAGE_NAMES[code] || code}</option>
            ))}
          </select>
        </label>
      </div>
      <label className="policy-field">来源
        <input id={EDITOR_IDS.source} type="text" minLength={1} maxLength={500} placeholder="https://… 或 internal:…" required {...field("sourceUrl")} />
      </label>
      <div className="policy-editor-actions">
        <button className="button button-secondary" type="button" onClick={() => dispatch({ type: "RESET_EDITOR" })}>
          重置
        </button>
        <button className="button button-primary" type="submit" disabled={editor.busy}>
          <svg className="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#check" /></svg>
          <span>{editing ? "保存修改" : "保存草稿"}</span>
        </button>
    </div>
    </form>
  );
}
