/**
 * Helix Support — attachment surface (ROADMAP §41.6 / ARC-001).
 *
 * Voice/rich-message attachments: pending-per-conversation uploads, chip
 * rendering, name backfill. Extracted from the legacy app.js; app.js keeps
 * thin delegating wrappers with identical names/signatures, so the running
 * UI behaviour is unchanged. app.js calls configure() once at load time.
 *
 * ROADMAP H02 (2.19.0): an upload that fails used to drop the picked File on
 * the floor — the queue was only ever appended to on success, so a 413 quota
 * error or a flaky network left the operator to find the file on disk again.
 * That is the "lose the draft" half of H02's acceptance clause. Failures are
 * now retained per conversation and addressable, the retry reuses the upload
 * receipt key, and nothing is discarded without an explicit dismissal.
 */

import { clearUploadKey, uploadKeyFor, uploadTokenFor } from "./composer-command.js?v=1.4.0";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

// pending 附件按会话隔离(conversationId → id[]):上传/移除/发送/清除全部
// 作用在当前会话,避免 A 会话已上传的附件在切到 B 会话发送时被错误挂载。
let pendingAttachmentsByConv = {};
// 失败待重试(conversationId → [{token, file, filename, size, error}]):保留
// File 本身,让坐席可以在不清空文件选择框的情况下重试同一份文件。同样按会话
// 隔离,并且有界 —— 内存里攥着 File 引用不能无上限。
let failedUploadsByConv = {};
/** Bounded: an operator can accumulate failures faster than they dismiss them. */
const MAX_FAILED_UPLOADS = 5;
// Backlog: 语音/富媒体消息 — ``attachmentMetaById`` 缓存整个 AttachmentOut
// (filename/content_type/size),供 chip 渲染缩略图与判断图片类型。
let attachmentMetaById = {};
let attachmentNamesLoaded = {};

/** Pending attachment ids for a conversation (read-only; composer reads it). */
export function pendingIds(conversationId) {
  return pendingAttachmentsByConv[conversationId] || [];
}

/** Failed uploads awaiting a retry or an explicit dismissal (read-only copy). */
export function failedAttachments(conversationId) {
  return (failedUploadsByConv[conversationId] || []).map((entry) => ({ ...entry }));
}

/** Retain a failed attempt, replacing any earlier record of the same file so a
 *  repeated retry does not queue the file twice, and keep the queue bounded. */
function rememberFailedUpload(conversationId, entry) {
  const list = (failedUploadsByConv[conversationId] || []).filter(
    (existing) => existing.token !== entry.token,
  );
  list.push(entry);
  if (list.length > MAX_FAILED_UPLOADS) list.splice(0, list.length - MAX_FAILED_UPLOADS);
  failedUploadsByConv[conversationId] = list;
}

function forgetFailedUpload(conversationId, token) {
  const list = failedUploadsByConv[conversationId];
  if (!list) return;
  const next = list.filter((entry) => entry.token !== token);
  if (next.length) failedUploadsByConv[conversationId] = next;
  else delete failedUploadsByConv[conversationId];
}

/** Copy of the known attachment metadata for the thread island snapshot
 * (chips render filenames after loadAttachmentNames resolves). */
export function attachmentMetaSnapshot() {
  const out = {};
  for (const [id, meta] of Object.entries(attachmentMetaById)) {
    out[id] = { filename: meta.filename, content_type: meta.content_type };
  }
  return out;
}

export function attachmentChips(ids) {
  return (ids || [])
    .map((id) => {
      const meta = attachmentMetaById[id] || {};
      const name = meta.filename || id;
      const href = `/api/attachments/${encodeURIComponent(id)}/download`;
      // 图片(安全子集,排除 svg)渲染懒加载缩略图预览;其余走文件下载链接。
      const isImage = /^image\/(png|jpe?g|gif|webp)$/.test(meta.content_type || "");
      return isImage
        ? `<a class="attachment-chip is-image" href="${href}" data-id="${ctx.escapeHtml(id)}" target="_blank" rel="noopener" title="${ctx.escapeHtml(name)} — 点击打开预览">` +
            `<img class="attachment-thumb" src="${href}" alt="" loading="lazy">` +
            `<span class="attachment-chip-name">${ctx.escapeHtml(name)}</span></a>`
        : `<a class="attachment-chip" href="${href}" data-id="${ctx.escapeHtml(id)}" target="_blank" rel="noopener" title="下载 ${ctx.escapeHtml(name)}">${ctx.icon("file-text")}<span class="attachment-chip-name">${ctx.escapeHtml(name)}</span></a>`;
    })
    .join("");
}

export function patchAttachmentChips() {
  document.querySelectorAll(".attachment-chip").forEach((chip) => {
    const id = chip.dataset.id;
    const meta = attachmentMetaById[id];
    if (!meta || !meta.filename) return;
    const label = chip.querySelector(".attachment-chip-name");
    if (label) label.textContent = meta.filename;
  });
}

export async function loadAttachmentNames(conversationId) {
  if (!conversationId || attachmentNamesLoaded[conversationId]) return;
  attachmentNamesLoaded[conversationId] = true;
  try {
    const items = await ctx.api(`/api/attachments?conversation_id=${encodeURIComponent(conversationId)}`);
    for (const item of items || []) attachmentMetaById[item.id] = item;
    patchAttachmentChips();
  } catch {
    /* names stay at the id fallback */
  }
}

/**
 * Upload one file, carrying the upload receipt key.
 *
 * On success the attachment joins this conversation's pending set and any
 * failure recorded for the same file is dropped. On failure the File is
 * *retained* (see ``failedAttachments``) so the operator can retry the same
 * bytes; the caller's file input is still cleared, because that value only
 * gates re-selecting a file and is not the recovery mechanism.
 *
 * The attempt is bound to the conversation it started in — switching mid
 * upload cannot attach the result to the conversation now on screen.
 */
async function uploadAttempt(conversationId, file) {
  const token = uploadTokenFor(file);
  const form = new FormData();
  form.append("conversation_id", conversationId);
  form.append("file", file, file.name);
  try {
    const attachment = await ctx.api("/api/attachments", {
      method: "POST",
      headers: { "Idempotency-Key": uploadKeyFor(conversationId, file) },
      body: form,
    });
    clearUploadKey(conversationId, file);
    forgetFailedUpload(conversationId, token);
    (pendingAttachmentsByConv[conversationId] = pendingAttachmentsByConv[conversationId] || []).push(
      attachment.id,
    );
    attachmentMetaById[attachment.id] = attachment;
    renderPendingAttachments();
    return attachment;
  } catch (error) {
    const message = error?.message || String(error);
    rememberFailedUpload(conversationId, {
      token,
      file,
      filename: file.name,
      size: file.size,
      error: message,
    });
    renderPendingAttachments();
    ctx.showToast(`附件上传失败：${message}`, true);
    return null;
  } finally {
    if (ctx.els.attachmentFile) ctx.els.attachmentFile.value = "";
  }
}

export async function uploadPendingAttachment(file, options = {}) {
  const conversationId = options.conversationId || ctx.state.selectedId;
  if (!conversationId || !file) return null;
  return uploadAttempt(conversationId, file);
}

/** Retry a retained upload with the same attempt key, so a lost response
 *  resolves to the attachment the server already stored. */
export async function retryFailedAttachment(conversationId, token) {
  const entry = (failedUploadsByConv[conversationId] || []).find((item) => item.token === token);
  if (!entry) return null;
  return uploadAttempt(conversationId, entry.file);
}

/** Explicitly discard a failed upload (never implicit — see H02 "不丢稿"). */
export function dismissFailedAttachment(conversationId, token) {
  if (!conversationId || !token) return;
  forgetFailedUpload(conversationId, token);
  renderPendingAttachments();
}

function failedAttachmentChips(conversationId) {
  return (failedUploadsByConv[conversationId] || [])
    .map(
      (entry) =>
        `<span class="pending-attachment-chip is-failed" title="${ctx.escapeHtml(entry.error)}">` +
        `${ctx.escapeHtml(entry.filename)}` +
        `<button type="button" class="failed-attachment-retry" data-token="${ctx.escapeHtml(entry.token)}" title="重试上传">重试</button>` +
        `<button type="button" class="failed-attachment-dismiss" data-token="${ctx.escapeHtml(entry.token)}" title="放弃这个附件">×</button>` +
        `</span>`,
    )
    .join("");
}

export function renderPendingAttachments() {
  // Island mode: publish the composer state snapshot (the composer island
  // renders the pending chips from it); legacy keeps painting its DOM.
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    window.HelixModules?.composerIslandBridge?.publishComposerState?.();
    return;
  }
  if (!ctx.els.pendingAttachments) return;
  const conversationId = ctx.state.selectedId;
  const ids = conversationId ? pendingAttachmentsByConv[conversationId] || [] : [];
  const stored = ids
    .map((id) => `<span class="pending-attachment-chip">${ctx.escapeHtml(attachmentMetaById[id]?.filename || id)} <button type="button" class="pending-attachment-remove" data-id="${ctx.escapeHtml(id)}" title="移除">×</button></span>`)
    .join("");
  const failed = conversationId ? failedAttachmentChips(conversationId) : "";
  ctx.els.pendingAttachments.innerHTML = stored + failed;
}

export function clearPendingAttachments(conversationId) {
  const key = conversationId || ctx.state.selectedId;
  if (key) delete pendingAttachmentsByConv[key];
  renderPendingAttachments();
}

/**
 * Discard the whole attachment surface state (staged files, retained
 * failures, cached metadata). Mirrors ``composer-command.js``'s ``reset``:
 * for tearing the contract down as a whole — an operator identity change, or
 * test isolation — never for a mere conversation switch, which is already
 * scoped per conversation.
 */
export function resetAttachments() {
  pendingAttachmentsByConv = {};
  failedUploadsByConv = {};
  attachmentMetaById = {};
  attachmentNamesLoaded = {};
}

export function renderAttachmentBar(detail) {
  const conversation = detail.conversation;
  const human = ["waiting_human", "human_active"].includes(conversation.status);
  const visible = human && ctx.canOperate();
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    // The island derives the bar visibility from the composer state snapshot.
    if (!visible) delete pendingAttachmentsByConv[conversation.id];
    renderPendingAttachments();
    return;
  }
  if (!ctx.els.attachmentBar) return;
  ctx.els.attachmentBar.hidden = !visible;
  // Only the *staged* set is dropped when the bar goes away — those files were
  // being prepared for a send that can no longer happen. Retained failures
  // survive: they are what the operator is owed a retry for, and the queue is
  // already bounded.
  if (ctx.els.attachmentBar.hidden) delete pendingAttachmentsByConv[conversation.id];
  renderPendingAttachments();
}

/** Shared pending-attachment removal (legacy chip click + island bridge). */
export function removePendingAttachment(conversationId, id) {
  if (!conversationId || !id) return;
  pendingAttachmentsByConv[conversationId] = (
    pendingAttachmentsByConv[conversationId] || []
  ).filter((existing) => existing !== id);
  renderPendingAttachments();
}

/**
 * Bind the attachment upload/chip DOM (exactly once, at app.js load).
 */
export function bindAttachments() {
  if (!ctx?.els) return false;
  if (ctx.els.attachmentFile) {
    ctx.els.attachmentFile.addEventListener("change", () => {
      if (ctx.els.attachmentFile.files?.length) void uploadPendingAttachment(ctx.els.attachmentFile.files[0]);
    });
  }
  if (ctx.els.pendingAttachments) {
    ctx.els.pendingAttachments.addEventListener("click", (event) => {
      const retry = event.target.closest(".failed-attachment-retry");
      if (retry) {
        void retryFailedAttachment(ctx.state.selectedId, retry.dataset.token);
        return;
      }
      const dismiss = event.target.closest(".failed-attachment-dismiss");
      if (dismiss) {
        dismissFailedAttachment(ctx.state.selectedId, dismiss.dataset.token);
        return;
      }
      const button = event.target.closest(".pending-attachment-remove");
      if (!button) return;
      removePendingAttachment(ctx.state.selectedId, button.dataset.id);
    });
  }
  return true;
}