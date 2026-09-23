// Helix Guard — policy view lifecycle unit tests (D3 long tail slice 17)
// Run: node --test tests/frontend/knowledge-view.test.js

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  KNOWLEDGE_EVENTS,
  configure,
  loadKnowledgeView,
  renderKnowledgeSummary,
} from "../../app/static/js/policy-view.js";

const ARTICLES = [
  { id: "k1", title: "配送时效", content: "24 小时内出库。", category: "logistics", status: "published", language: "zh", tags: ["配送"], version: 2 },
  { id: "k2", title: "退款政策", content: "三个工作日到账。", category: "billing", status: "draft", language: "zh", tags: ["退款"], version: 1 },
];

/** Minimal window stub with an EventTarget and the island-mode flag. */
function installWindow({ islandMode } = {}) {
  const target = new EventTarget();
  target.__HELIX_ISLAND_MODE__ = Boolean(islandMode);
  target.setTimeout = (...args) => setTimeout(...args);
  target.clearTimeout = (...args) => clearTimeout(...args);
  target.confirm = () => true;
  globalThis.window = target;
  return target;
}

function stubEls() {
  const el = () => ({ innerHTML: "", hidden: true, textContent: "", value: "", options: [], setAttribute() {}, getAttribute: () => null, addEventListener() {}, insertAdjacentHTML() {}, reset() {}, focus() {} });
  return {
    policyView: el(),
    policySearch: el(),
    policyStatusFilter: el(),
    policyLanguageFilter: el(),
    policyLanguage: el(),
    policySummary: el(),
    policyList: el(),
    policyListStatus: el(),
    policyResultCount: el(),
    policyReadOnly: el(),
    policyEditor: el(),
    policyForm: el(),
    policyTitle: el(),
    policyContent: el(),
    policyTags: el(),
    policyCategory: el(),
    policySource: el(),
    policyEditorTitle: el(),
    policySaveLabel: el(),
    newPolicyDraft: el(),
    refreshPolicy: el(),
    cancelPolicyEdit: el(),
    resetPolicyForm: el(),
  };
}

function configureDeps({ writer = true, api, overrides = {} } = {}) {
  const els = stubEls();
  const apiCalls = [];
  configure({
    state: {
      me: { permissions: writer ? ["knowledge:write"] : [] },
      knowledgeArticles: [],
      knowledgeLoadedAt: 0,
      knowledgeLoadedForWriter: null,
      knowledgeEditingId: null,
    },
    els,
    api: api || (async (url) => { apiCalls.push(url); return ARTICLES; }),
    showToast: async () => {},
    setFormBusy: () => {},
    escapeHtml: (v) => String(v ?? ""),
    formatTime: () => "08:00",
    languageNames: { zh: "中文", en: "English" },
    ...overrides,
  });
  return { els, apiCalls };
}

beforeEach(() => {
  delete globalThis.window;
});

test("island-mode loadKnowledgeView hands the refresh over without fetching", async () => {
  const windowStub = installWindow({ islandMode: true });
  let dispatched = null;
  windowStub.addEventListener(KNOWLEDGE_EVENTS.REFRESH, (event) => { dispatched = event.detail; });
  const { apiCalls, els } = configureDeps();
  await loadKnowledgeView({ force: true });
  assert.deepEqual(dispatched, { force: true });
  assert.equal(apiCalls.length, 0, "no fetch in island mode");
  assert.equal(els.policyList.innerHTML, "");
});

test("legacy loadKnowledgeView fetches, caches and renders the list", async () => {
  installWindow({ islandMode: false });
  let apiCalls = 0;
  const { els } = configureDeps({
    api: async () => { apiCalls += 1; return ARTICLES; },
  });
  await loadKnowledgeView({ force: true });
  assert.match(els.policyList.innerHTML, /配送时效/);
  assert.match(els.policySummary.innerHTML, /已发布/);
  await loadKnowledgeView(); // within the 15s cache — no refetch
  assert.equal(apiCalls, 1);
});

test("renderKnowledgeSummary hides draft counters from readers", () => {
  installWindow({ islandMode: false });
  const { els } = configureDeps({ writer: false, overrides: { state: { me: { permissions: [] }, knowledgeArticles: ARTICLES, knowledgeLoadedAt: 0, knowledgeLoadedForWriter: null, knowledgeEditingId: null } } });
  renderKnowledgeSummary();
  assert.match(els.policySummary.innerHTML, /<strong>—<\/strong>/);
});
