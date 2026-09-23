/* Helix Web Chat — framework-free client primitives (ROADMAP 17.3). */

export const WIDGET_ACCENTS = Object.freeze({
  teal: { name: "teal", label: "Teal" },
  blue: { name: "blue", label: "Blue" },
  amber: { name: "amber", label: "Amber" },
  violet: { name: "violet", label: "Violet" },
  coral: { name: "coral", label: "Coral" },
});

const COPY = {
  zh: {
    brand: "Helix Guard",
    eyebrow: "在线提交入口",
    greeting: "你好，我是审核助手。告诉我你正在处理什么，我们一起找到下一步。",
    nameLabel: "怎么称呼你？（可选）",
    namePlaceholder: "例如：林晓",
    start: "开始提交",
    inputLabel: "发送消息",
    inputPlaceholder: "输入你的问题…",
    send: "发送",
    sending: "发送中",
    loading: "正在恢复提交…",
    online: "在线",
    reconnecting: "正在重新连接…",
    timeout: "消息已收到，复核仍在处理中。",
    handoff: "已转交人工复核，我们会尽快回复。",
    error: "连接遇到一点问题，请稍后再试。",
    expired: "这条提交链接已失效，请重新打开提交入口。",
    intro: "你的消息会安全地送交人工复核。",
    powered: "由 Helix Guard 提供支持",
    chatTitle: "内容提交",
    fatalTitle: "提交链接不可用",
  },
  en: {
    brand: "Helix Guard",
    eyebrow: "SUBMISSION INTAKE",
    greeting: "Hi, I’m your review assistant. Tell me what you’re working through and we’ll find the next step.",
    nameLabel: "What should we call you? (optional)",
    namePlaceholder: "For example: Alex",
    start: "Start a submission",
    inputLabel: "Send a message",
    inputPlaceholder: "Type your question…",
    send: "Send",
    sending: "Sending",
    loading: "Restoring your submission…",
    online: "Online",
    reconnecting: "Reconnecting…",
    timeout: "Your message is in. Review is still in progress.",
    handoff: "Your submission is with a reviewer now.",
    error: "The connection hit a snag. Please try again.",
    expired: "This submission link has expired. Please reopen the intake entry point.",
    intro: "Your submission travels securely to human review.",
    powered: "Powered by Helix Guard",
    chatTitle: "Content submission",
    fatalTitle: "Submission link unavailable",
  },
};

const DEFAULT_CONFIG = Object.freeze({
  brand: "Helix Guard",
  greeting: "",
  accent: "teal",
  locale: "zh",
  token: "",
});

function clampText(value, fallback, max = 120) {
  const text = String(value ?? "").trim().slice(0, max);
  return text || fallback;
}

function decodeBase64Url(value) {
  try {
    const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
    if (typeof globalThis.atob === "function") return globalThis.atob(padded);
    if (globalThis.Buffer) return globalThis.Buffer.from(padded, "base64").toString("utf8");
  } catch (_error) {
    return "";
  }
  return "";
}

export function tokenPayload(token) {
  const encoded = String(token || "").split(".")[0];
  if (!encoded) return {};
  try {
    return JSON.parse(decodeBase64Url(encoded) || "{}");
  } catch (_error) {
    return {};
  }
}

export function normalizeLocale(value) {
  return String(value || "").toLowerCase().startsWith("en") ? "en" : "zh";
}

export function normalizeAccent(value) {
  const key = String(value || "").toLowerCase().trim();
  return Object.hasOwn(WIDGET_ACCENTS, key) ? key : DEFAULT_CONFIG.accent;
}

export function readWidgetConfig(input = globalThis.location) {
  const url = new URL(String(input?.href || input || "http://widget.local/widget"));
  const query = url.searchParams;
  const hash = new URLSearchParams(url.hash.replace(/^#/, ""));
  const locale = normalizeLocale(query.get("locale") || hash.get("locale"));
  return {
    brand: clampText(query.get("brand"), DEFAULT_CONFIG.brand),
    greeting: clampText(query.get("greeting"), COPY[locale].greeting, 280),
    accent: normalizeAccent(query.get("accent")),
    locale,
    token: clampText(hash.get("token"), "", 4096),
  };
}

export function copy(locale, key, params = {}) {
  let value = COPY[normalizeLocale(locale)][key] || COPY.zh[key] || key;
  return value.replace(/\{(\w+)\}/g, (_match, name) => String(params[name] ?? `{${name}}`));
}

export function sessionKey() {
  return "helix-widget-session";
}

export function restoreSession(storage, key = sessionKey()) {
  try {
    const parsed = JSON.parse(storage.getItem(key) || "null");
    if (!parsed?.conversationId || !parsed?.token) return null;
    const payload = tokenPayload(parsed.token);
    if (payload.exp && Number(payload.exp) * 1000 <= Date.now()) {
      storage.removeItem(key);
      return null;
    }
    return { conversationId: String(parsed.conversationId), token: String(parsed.token) };
  } catch (_error) {
    storage.removeItem(key);
    return null;
  }
}

export function restoreSessionForLaunch(storage, bootstrapToken, key = sessionKey()) {
  if (String(bootstrapToken || "").trim()) {
    clearSession(storage, key);
    return null;
  }
  return restoreSession(storage, key);
}

export function saveSession(storage, session, key = sessionKey()) {
  storage.setItem(key, JSON.stringify(session));
}

export function clearSession(storage, key = sessionKey()) {
  storage.removeItem(key);
}

export function makeChannelMessageId(cryptoObject = globalThis.crypto) {
  if (cryptoObject?.randomUUID) return `web-${cryptoObject.randomUUID()}`;
  const random = Math.random().toString(36).slice(2);
  return `web-${Date.now().toString(36)}-${random}`;
}

export function parseSseFrames(buffer, flush = false) {
  const normalized = String(buffer || "").replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const remainder = flush ? "" : parts.pop() || "";
  const events = [];
  for (const block of parts) {
    if (!block.trim() || block.trimStart().startsWith(":")) continue;
    let event = "message";
    const data = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (data.length) events.push({ event, data: data.join("\n") });
  }
  return { events, remainder };
}

export function mergeMessages(existing, incoming) {
  const byId = new Map();
  for (const message of [...(existing || []), ...(incoming || [])]) {
    if (!message?.id || ["internal", "internal_note"].includes(message.role)) continue;
    byId.set(String(message.id), message);
  }
  return [...byId.values()].sort((left, right) => {
    const time = String(left.created_at || "").localeCompare(String(right.created_at || ""));
    return time || Number(left.seq || 0) - Number(right.seq || 0);
  });
}

export function messageTone(role) {
  if (role === "customer") return "customer";
  if (role === "operator") return "operator";
  return "assistant";
}

/* H01 — resumable send and idle polling.
 *
 * These live here rather than in the entry point because they are the parts
 * that must not be wrong: whether a retry is the same send attempt (a second
 * key would produce a second customer message), whether a poll may run at all,
 * and how far the cursor may move (too far skips a reply, moving on a failed
 * page re-reads forever).
 */

export const POLL_BASE_DELAY_MS = 4000;
export const POLL_MAX_DELAY_MS = 30000;

const MAX_BACKOFF_STEPS = 8;

/**
 * Decide the send attempt for `content`.
 *
 * The same text retried after a failure is the *same* attempt: it keeps its
 * channel id, so the server resolves it as a replay instead of creating a
 * second customer message when the first confirmation was lost. Edited text is
 * a different message and gets a fresh id.
 */
export function nextSendAttempt(previous, content, idFactory = makeChannelMessageId) {
  const text = String(content ?? "").trim();
  if (!text) return null;
  if (previous?.channelMessageId && String(previous.content || "") === text) {
    return previous;
  }
  return { channelMessageId: String(idFactory()), content: text };
}

/**
 * Fold a polled delta into the transcript.
 *
 * Optimistic `local-*` bubbles are dropped first: once the server transcript is
 * being read the confirmed message is the source of truth, and keeping the
 * placeholder would render the customer's own message twice.
 */
export function mergePolledMessages(current, incoming) {
  const settled = (current || []).filter(
    (message) => !String(message?.id || "").startsWith("local-"),
  );
  return mergeMessages(settled, incoming);
}

/** Map the conversation status headers onto the banner state. */
export function conversationSignals(status, surveyUrl) {
  const normalized = String(status || "");
  const url = String(surveyUrl || "");
  const resolved = normalized === "resolved" && Boolean(url);
  return {
    handoff: normalized === "waiting_human" || normalized === "human_active",
    resolved,
    resolvedSurveyUrl: resolved ? url : "",
  };
}

/** Poll only while an idle, visible conversation can still change. */
export function shouldPoll(state = {}) {
  return Boolean(state.conversationId) && !state.resolved && !state.busy && !state.hidden;
}

/** Exponential backoff for a failing poll, capped at `max`. */
export function nextPollDelayMs(failures, base = POLL_BASE_DELAY_MS, max = POLL_MAX_DELAY_MS) {
  const count = Number(failures);
  const step = Number.isFinite(count) ? Math.max(0, Math.min(Math.trunc(count), MAX_BACKOFF_STEPS)) : 0;
  return Math.min(base * 2 ** step, max);
}

/** Move the cursor only on a confirmed page; an empty answer keeps position. */
export function advanceCursor(current, candidate) {
  return String(candidate || "") || String(current || "");
}

export const DEFAULT_WIDGET_CONFIG = DEFAULT_CONFIG;
