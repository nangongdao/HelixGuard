// Helix Guard — composer cross-conversation consistency tests (ROADMAP H02)
// Run: node --test tests/frontend/composer-consistency.test.js
//
// The acceptance scenarios for H02: an unfinished request for A, a slow
// rewrite while the operator keeps typing, a double submit / retry after a
// timeout, and a send confirmation that lands after the operator has moved to
// B. None of them may cross conversations or lose the draft.
//
// The api stub resolves through explicit deferreds so the tests can decide
// what happens *while* a command is in flight — which is the whole point.

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  applyCopilotTone,
  bumpDraftVersion,
  clearPendingAttachments,
  configure as configureComposer,
  configureAttachments,
  fetchCopilotSuggestions,
  loadCopilotKnowledge,
  pendingIds,
  reset,
  sendOperatorMessage,
  uploadPendingAttachment,
} from "../../app/static/js/composer.js";
import { escapeHtml } from "../../app/static/js/format.js";

const SUGGESTIONS = [
  { content: "建议回复一", source: "model" },
  { content: "建议回复二", source: "canned" },
];

function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

/** Minimal window stub (the legacy modules reach for setTimeout and
 *  localStorage; island mode stays off so the DOM path is exercised). */
function installWindow() {
  const store = new Map();
  const target = new EventTarget();
  target.__HELIX_ISLAND_MODE__ = false;
  target.setTimeout = (...args) => setTimeout(...args);
  target.clearTimeout = (...args) => clearTimeout(...args);
  target.localStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
    get length() {
      return store.size;
    },
    key: (index) => [...store.keys()][index] ?? null,
  };
  globalThis.window = target;
  return target;
}

function configureAll({ apiImpl } = {}) {
  const els = {
    operatorInput: { value: "" },
    operatorForm: { dataset: {} },
    copilotSuggestions: { hidden: true, innerHTML: "" },
    copilotKnowledge: { hidden: true, innerHTML: "" },
    copilotStatus: { hidden: true, textContent: "" },
    pendingAttachments: { innerHTML: "" },
    attachmentFile: { value: "" },
  };
  const state = {
    selectedId: "conv-a",
    lastCopilotConv: null,
    cannedResponses: [],
    cannedLoadedAt: 0,
  };
  const calls = [];
  const toasts = [];
  let detailLoads = 0;
  const bundle = {
    state,
    els,
    api: async (url, options) => {
      calls.push({ url, options });
      return apiImpl ? apiImpl(url, options) : {};
    },
    canOperate: () => true,
    escapeHtml,
    setFormBusy: () => {},
    showToast: (message, isError) => toasts.push({ message, isError }),
    loadDetail: async () => {
      detailLoads += 1;
    },
    refreshAll: () => {},
  };
  configureComposer(bundle);
  configureAttachments(bundle);
  return { state, els, calls, toasts, detailLoads: () => detailLoads };
}

beforeEach(() => {
  reset();
  installWindow();
});

test("a suggestion issued for A never paints into B", async () => {
  const gate = deferred();
  const { state, els } = configureAll({
    apiImpl: (url) => (url === "/api/copilot/suggest" ? gate.promise : {}),
  });
  const inFlight = fetchCopilotSuggestions({ draft: "A 的草稿" });
  state.selectedId = "conv-b";
  gate.resolve({ suggestions: SUGGESTIONS });
  await inFlight;
  assert.equal(els.copilotSuggestions.innerHTML, "");
  assert.equal(els.copilotSuggestions.hidden, true);
});

test("a rewrite does not overwrite text typed while it was in flight", async () => {
  const gate = deferred();
  const { els } = configureAll({
    apiImpl: (url) => (url === "/api/copilot/rewrite" ? gate.promise : {}),
  });
  els.operatorInput.value = "原始草稿";
  const inFlight = applyCopilotTone("concise", { text: "原始草稿" });
  // The operator keeps typing: the bridge bumps the draft generation.
  bumpDraftVersion("conv-a");
  els.operatorInput.value = "原始草稿，另外再补充一句";
  gate.resolve({ rewritten: "改写后", source: "model" });
  await inFlight;
  assert.equal(els.operatorInput.value, "原始草稿，另外再补充一句");
  assert.match(els.copilotStatus.textContent, /草稿已更新/);
});

test("a rewrite issued for A does not write into B's composer", async () => {
  const gate = deferred();
  const { state, els } = configureAll({
    apiImpl: (url) => (url === "/api/copilot/rewrite" ? gate.promise : {}),
  });
  els.operatorInput.value = "A 的草稿";
  const inFlight = applyCopilotTone("concise", { text: "A 的草稿" });
  state.selectedId = "conv-b";
  els.operatorInput.value = "B 的草稿";
  gate.resolve({ rewritten: "改写后", source: "model" });
  await inFlight;
  assert.equal(els.operatorInput.value, "B 的草稿");
});

test("a send confirmation landing after a switch leaves B's composer alone", async () => {
  const gate = deferred();
  const { state, els, detailLoads } = configureAll({
    apiImpl: (url) => (url.includes("/operator-messages") ? gate.promise : {}),
  });
  els.operatorInput.value = "A 的回复";
  const inFlight = sendOperatorMessage("A 的回复");
  state.selectedId = "conv-b";
  els.operatorInput.value = "B 正在输入";
  gate.resolve({ id: "msg_1" });
  await inFlight;
  assert.equal(els.operatorInput.value, "B 正在输入");
  assert.equal(detailLoads(), 0);
});

test("a send carries an idempotency key and clears its own composer", async () => {
  const { els, calls, detailLoads, toasts } = configureAll({
    apiImpl: (url) => (url.includes("/operator-messages") ? { id: "msg_1" } : {}),
  });
  els.operatorInput.value = "已为您加急处理";
  await sendOperatorMessage("已为您加急处理");
  const post = calls.find((call) => call.url.includes("/operator-messages"));
  assert.ok(post, "the reply was posted");
  assert.ok(post.options.headers["Idempotency-Key"], "the attempt is identified");
  assert.equal(els.operatorInput.value, "");
  assert.equal(detailLoads(), 1);
  assert.deepEqual(toasts, []);
});

test("a double submit reuses one attempt key instead of sending twice", async () => {
  const { els, calls } = configureAll({
    apiImpl: (url) => (url.includes("/operator-messages") ? { id: "msg_1" } : {}),
  });
  await Promise.all([sendOperatorMessage("已为您加急"), sendOperatorMessage("已为您加急")]);
  const keys = calls
    .filter((call) => call.url.includes("/operator-messages"))
    .map((call) => call.options.headers["Idempotency-Key"]);
  assert.equal(keys.length, 2);
  assert.equal(new Set(keys).size, 1);
});

test("a failed send keeps the draft and retries with the same key", async () => {
  let failing = true;
  const { els, calls, toasts } = configureAll({
    apiImpl: (url) => {
      if (!url.includes("/operator-messages")) return {};
      return failing ? Promise.reject(new Error("Request timed out")) : { id: "msg_1" };
    },
  });
  els.operatorInput.value = "已为您加急";
  await sendOperatorMessage("已为您加急");
  assert.equal(els.operatorInput.value, "已为您加急", "the draft survives a failed send");
  assert.equal(toasts.length, 1);
  failing = false;
  await sendOperatorMessage("已为您加急");
  const keys = calls
    .filter((call) => call.url.includes("/operator-messages"))
    .map((call) => call.options.headers["Idempotency-Key"]);
  assert.equal(new Set(keys).size, 1, "the retry resolves to the same attempt");
  assert.equal(els.operatorInput.value, "");
});

test("a failed send keeps the pending attachments for the retry", async () => {
  let failing = true;
  const { els, calls } = configureAll({
    apiImpl: (url) => {
      if (url === "/api/attachments") return { id: "att_1", filename: "a.png" };
      if (!url.includes("/operator-messages")) return {};
      return failing ? Promise.reject(new Error("Request timed out")) : { id: "msg_1" };
    },
  });
  await uploadPendingAttachment(Object.assign(new Blob(["png"]), { name: "a.png" }));
  assert.deepEqual(pendingIds("conv-a"), ["att_1"]);
  await sendOperatorMessage("请看附件");
  assert.deepEqual(pendingIds("conv-a"), ["att_1"], "attachments stay retryable");
  const body = JSON.parse(calls.find((call) => call.url.includes("/operator-messages")).options.body);
  assert.deepEqual(body.attachment_ids, ["att_1"]);
  failing = false;
  await sendOperatorMessage("请看附件");
  assert.deepEqual(pendingIds("conv-a"), []);
  clearPendingAttachments("conv-a");
});

test("a knowledge load discarded by a switch reloads when the operator returns", async () => {
  const gate = deferred();
  const { state, calls } = configureAll({
    apiImpl: (url) => (url === "/api/copilot/knowledge" ? gate.promise : {}),
  });
  const inFlight = loadCopilotKnowledge();
  state.selectedId = "conv-b";
  gate.resolve({ articles: [{ title: "配送时效", category: "物流" }] });
  await inFlight;
  assert.equal(state.lastCopilotConv, null, "the cache stays cold for a discarded load");
  state.selectedId = "conv-a";
  await loadCopilotKnowledge();
  assert.equal(calls.filter((call) => call.url === "/api/copilot/knowledge").length, 2);
  assert.equal(state.lastCopilotConv, "conv-a");
});

test("a knowledge load paints and marks the conversation once applied", async () => {
  const { state, els } = configureAll({
    apiImpl: (url) =>
      url === "/api/copilot/knowledge" ? { articles: [{ title: "配送时效", category: "物流" }] } : {},
  });
  await loadCopilotKnowledge();
  assert.equal(state.lastCopilotConv, "conv-a");
  assert.match(els.copilotKnowledge.innerHTML, /配送时效/);
  // A second call short-circuits on the warm cache.
  await loadCopilotKnowledge();
  assert.equal(state.lastCopilotConv, "conv-a");
});

test("a confirmation does not wipe text typed while the send was in flight", async () => {
  const gate = deferred();
  const { els } = configureAll({
    apiImpl: (url) => (url.includes("/operator-messages") ? gate.promise : {}),
  });
  els.operatorInput.value = "已为您加急处理";
  const inFlight = sendOperatorMessage("已为您加急处理");
  // The operator keeps typing: the box no longer holds what was sent.
  els.operatorInput.value = "已为您加急处理，另外再补一句";
  gate.resolve({ id: "msg_1" });
  await inFlight;
  assert.equal(els.operatorInput.value, "已为您加急处理，另外再补一句");
});
