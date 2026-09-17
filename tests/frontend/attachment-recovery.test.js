// Helix Support — attachment failure recovery tests (ROADMAP H02, 2.19.0)
// Run: node --test tests/frontend/attachment-recovery.test.js
//
// The remaining half of H02's acceptance: "附件失败…不丢稿". An upload that
// fails (413 quota, 415 type, a flaky network) must not silently discard the
// file the operator just picked — they would have to go back to the disk and
// find it again, which is exactly the "lose the draft" failure the card names.
//
// The contract under test:
//   * a failed upload keeps the File, keyed by conversation, and offers a retry;
//   * the retry reuses the *same* upload attempt key, so a lost response cannot
//     store the bytes twice (mirrors the send receipt);
//   * a confirmed upload promotes the attachment and drops the failure entry;
//   * the queue is bounded and never crosses conversations;
//   * dismissing is an explicit discard, not a silent one.

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  clearPendingAttachments,
  configure as configureComposer,
  configureAttachments,
  dismissFailedAttachment,
  failedAttachments,
  pendingIds,
  reset,
  resetAttachments,
  retryFailedAttachment,
  uploadPendingAttachment,
} from "../../app/static/js/composer.js";
import { escapeHtml } from "../../app/static/js/format.js";

function pngFile(name, { size = 4, lastModified = 1700000000000 } = {}) {
  const blob = new Blob([new Uint8Array(size)]);
  return Object.assign(blob, { name, lastModified });
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
    pendingAttachments: { innerHTML: "" },
    attachmentFile: { value: "" },
  };
  const state = { selectedId: "conv-a", me: { actor_id: "admin" } };
  const calls = [];
  const toasts = [];
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
    loadDetail: async () => {},
    refreshAll: () => {},
  };
  configureComposer(bundle);
  configureAttachments(bundle);
  const uploads = () => calls.filter((call) => call.url === "/api/attachments");
  return { state, els, calls, toasts, uploads };
}

beforeEach(() => {
  reset();
  resetAttachments();
  installWindow();
});

test("a failed upload keeps the file and offers a retry", async () => {
  const { toasts } = configureAll({
    apiImpl: () => Promise.reject(new Error("附件超出配额")),
  });
  await uploadPendingAttachment(pngFile("截图.png"));

  const failed = failedAttachments("conv-a");
  assert.equal(failed.length, 1);
  assert.equal(failed[0].filename, "截图.png");
  assert.match(failed[0].error, /配额/);
  assert.ok(failed[0].token, "the entry is addressable for a retry");
  assert.deepEqual(pendingIds("conv-a"), [], "nothing is pending until it is stored");
  assert.equal(toasts.length, 1);
});

test("retrying reuses the same upload attempt key and promotes the file", async () => {
  let failing = true;
  const { uploads } = configureAll({
    apiImpl: (url) => {
      if (url !== "/api/attachments") return {};
      return failing
        ? Promise.reject(new Error("Request timed out"))
        : { id: "att_1", filename: "截图.png" };
    },
  });
  await uploadPendingAttachment(pngFile("截图.png"));
  const [token] = failedAttachments("conv-a").map((entry) => entry.token);

  failing = false;
  await retryFailedAttachment("conv-a", token);

  const keys = uploads().map((call) => call.options.headers["Idempotency-Key"]);
  assert.equal(keys.length, 2);
  assert.ok(keys[0], "the upload attempt is identified");
  assert.equal(new Set(keys).size, 1, "the retry resolves to the same attempt");
  assert.deepEqual(pendingIds("conv-a"), ["att_1"]);
  assert.deepEqual(failedAttachments("conv-a"), [], "the retry clears the failure");
});

test("a confirmed direct upload clears a stale failure for the same file", async () => {
  let failing = true;
  configureAll({
    apiImpl: (url) => {
      if (url !== "/api/attachments") return {};
      return failing ? Promise.reject(new Error("Request timed out")) : { id: "att_1" };
    },
  });
  const file = pngFile("截图.png");
  await uploadPendingAttachment(file);
  assert.equal(failedAttachments("conv-a").length, 1);

  failing = false;
  await uploadPendingAttachment(file);
  assert.deepEqual(failedAttachments("conv-a"), []);
  assert.deepEqual(pendingIds("conv-a"), ["att_1"]);
});

test("dismissing drops the retry without uploading again", async () => {
  const { uploads } = configureAll({
    apiImpl: () => Promise.reject(new Error("附件超出配额")),
  });
  await uploadPendingAttachment(pngFile("截图.png"));
  const [token] = failedAttachments("conv-a").map((entry) => entry.token);
  const before = uploads().length;

  dismissFailedAttachment("conv-a", token);
  assert.deepEqual(failedAttachments("conv-a"), []);
  assert.equal(uploads().length, before, "dismissing never re-uploads");
});

test("an unknown retry token is a no-op", async () => {
  const { uploads } = configureAll({ apiImpl: () => ({}) });
  await retryFailedAttachment("conv-a", "no-such-token");
  assert.equal(uploads().length, 0);
});

test("failures and pending uploads never cross conversations", async () => {
  let gate;
  const pending = new Promise((resolve) => {
    gate = resolve;
  });
  const { state } = configureAll({
    apiImpl: (url) => {
      if (url !== "/api/attachments") return {};
      return pending;
    },
  });
  const inFlight = uploadPendingAttachment(pngFile("a.png"));
  state.selectedId = "conv-b";
  gate({ id: "att_1", filename: "a.png" });
  await inFlight;

  assert.deepEqual(pendingIds("conv-a"), ["att_1"], "A keeps what A uploaded");
  assert.deepEqual(pendingIds("conv-b"), [], "B never inherits A's upload");
  assert.deepEqual(failedAttachments("conv-b"), []);
});

test("a failure recorded for A stays with A after a switch", async () => {
  const { state } = configureAll({ apiImpl: () => Promise.reject(new Error("nope")) });
  await uploadPendingAttachment(pngFile("a.png"));
  state.selectedId = "conv-b";
  assert.equal(failedAttachments("conv-a").length, 1);
  assert.deepEqual(failedAttachments("conv-b"), []);
});

test("the retry queue is bounded", async () => {
  configureAll({ apiImpl: () => Promise.reject(new Error("nope")) });
  for (let index = 0; index < 7; index += 1) {
    await uploadPendingAttachment(pngFile(`f${index}.png`, { size: index + 1 }));
  }
  const failed = failedAttachments("conv-a");
  assert.equal(failed.length, 5, "the queue stays bounded");
  assert.equal(
    failed.some((entry) => entry.filename === "f0.png"),
    false,
    "the oldest attempt is dropped",
  );
  assert.equal(
    failed.some((entry) => entry.filename === "f6.png"),
    true,
    "the newest attempt is kept",
  );
});

test("the pending bar renders retryable chips for a failed upload", async () => {
  const { els } = configureAll({
    apiImpl: () => Promise.reject(new Error("附件超出配额")),
  });
  await uploadPendingAttachment(pngFile("截图.png"));
  assert.match(els.pendingAttachments.innerHTML, /截图\.png/);
  assert.match(els.pendingAttachments.innerHTML, /failed-attachment-retry/);
  assert.match(els.pendingAttachments.innerHTML, /failed-attachment-dismiss/);
  assert.match(els.pendingAttachments.innerHTML, /配额/);
});

test("clearing the pending set leaves a failed upload retryable", async () => {
  configureAll({ apiImpl: () => Promise.reject(new Error("nope")) });
  await uploadPendingAttachment(pngFile("a.png"));
  clearPendingAttachments("conv-a");
  assert.equal(
    failedAttachments("conv-a").length,
    1,
    "the send lifecycle cannot discard an un-stored file",
  );
});
