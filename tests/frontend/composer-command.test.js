// Helix Support — composer command/receipt contract unit tests (ROADMAP H02)
// Run: node --test tests/frontend/composer-command.test.js
//
// js/composer-command.js owns the two facts that decide whether an async
// composer command may apply its result: which conversation it was issued for
// (plus a per-surface generation) and which draft it was computed from. It
// also owns the client half of the send receipt — the stable idempotency key
// that makes retrying a send safe.
//
// The module keeps its state in module-local maps, so every test starts with
// a reset() to stay independent.

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  beginCommand,
  bumpDraftVersion,
  clearSendKey,
  clearUploadKey,
  configure as configureCommand,
  draftVersionFor,
  isCurrent,
  isDraftUnchanged,
  reset,
  sendKeyFor,
  uploadKeyFor,
  uploadTokenFor,
} from "../../app/static/js/composer-command.js";

let state;

beforeEach(() => {
  state = { selectedId: "conv-a" };
  configureCommand({ state });
  reset();
});

test("a ticket is current while the operator stays on its conversation", () => {
  const ticket = beginCommand("copilot-suggest", "conv-a");
  assert.equal(ticket.conversationId, "conv-a");
  assert.equal(isCurrent(ticket), true);
});

test("a ticket stops being current once the operator switches conversation", () => {
  const ticket = beginCommand("copilot-suggest", "conv-a");
  state.selectedId = "conv-b";
  assert.equal(isCurrent(ticket), false);
});

test("a newer command on the same surface supersedes the older ticket", () => {
  const first = beginCommand("copilot-tone", "conv-a");
  const second = beginCommand("copilot-tone", "conv-a");
  assert.equal(isCurrent(first), false);
  assert.equal(isCurrent(second), true);
});

test("surfaces supersede independently", () => {
  const suggest = beginCommand("copilot-suggest", "conv-a");
  beginCommand("copilot-tone", "conv-a");
  assert.equal(isCurrent(suggest), true);
});

test("beginCommand falls back to the selected conversation", () => {
  assert.equal(beginCommand("operator-send").conversationId, "conv-a");
});

test("a keystroke invalidates a result computed from the older draft", () => {
  const ticket = beginCommand("copilot-tone", "conv-a");
  assert.equal(isDraftUnchanged(ticket), true);
  bumpDraftVersion("conv-a");
  assert.equal(isDraftUnchanged(ticket), false);
});

test("draft versions are per conversation", () => {
  const ticket = beginCommand("copilot-tone", "conv-a");
  bumpDraftVersion("conv-b");
  assert.equal(draftVersionFor("conv-a"), 0);
  assert.equal(isDraftUnchanged(ticket), true);
});

test("a ticket for another conversation is never draft-unchanged", () => {
  const ticket = beginCommand("copilot-tone", "conv-a");
  state.selectedId = "conv-b";
  assert.equal(isDraftUnchanged(ticket), false);
});

test("send keys are stable for the same attempt", () => {
  const first = sendKeyFor("conv-a", "您好", ["att-1"]);
  const retry = sendKeyFor("conv-a", "您好", ["att-1"]);
  assert.equal(first, retry);
  assert.match(first, /^[0-9a-f]+$/);
});

test("send key attachment order does not matter", () => {
  assert.equal(sendKeyFor("conv-a", "您好", ["a1", "a2"]), sendKeyFor("conv-a", "您好", ["a2", "a1"]));
});

test("editing the text yields a new send key", () => {
  const original = sendKeyFor("conv-a", "您好");
  assert.notEqual(sendKeyFor("conv-a", "您好，请稍等"), original);
});

test("changing the attachment set yields a new send key", () => {
  const original = sendKeyFor("conv-a", "您好", []);
  assert.notEqual(sendKeyFor("conv-a", "您好", ["att-1"]), original);
});

test("the same text in another conversation is a different attempt", () => {
  const a = sendKeyFor("conv-a", "您好");
  const b = sendKeyFor("conv-b", "您好");
  assert.notEqual(a, b);
});

test("a confirmed attempt releases its key, so resending is a new attempt", () => {
  const first = sendKeyFor("conv-a", "您好");
  clearSendKey("conv-a", "您好");
  assert.notEqual(sendKeyFor("conv-a", "您好"), first);
});

test("reset drops every ticket, draft version and send key", () => {
  const ticket = beginCommand("operator-send", "conv-a");
  bumpDraftVersion("conv-a");
  const before = sendKeyFor("conv-a", "您好");
  reset();
  assert.equal(isCurrent(ticket), false);
  assert.equal(draftVersionFor("conv-a"), 0);
  assert.notEqual(sendKeyFor("conv-a", "您好"), before);
});

// ROADMAP H02 (2.19.0): upload attempts carry the same kind of receipt key, so
// a retry after a lost response resolves to the attachment the server already
// stored instead of storing the bytes — and charging the quota — twice.
test("the same file in the same conversation is one upload attempt", () => {
  const file = { name: "截图.png", size: 2048, lastModified: 1700000000000, type: "image/png" };
  const first = uploadKeyFor("conv-a", file);
  assert.equal(uploadKeyFor("conv-a", file), first);
  assert.equal(uploadTokenFor(file), uploadTokenFor({ ...file }), "the token is derived, not random");
});

test("a different file or another conversation is a different upload attempt", () => {
  const file = { name: "截图.png", size: 2048, lastModified: 1700000000000, type: "image/png" };
  const first = uploadKeyFor("conv-a", file);
  assert.notEqual(uploadKeyFor("conv-a", { ...file, size: 2049 }), first);
  assert.notEqual(uploadKeyFor("conv-a", { ...file, name: "另一张.png" }), first);
  assert.notEqual(uploadKeyFor("conv-b", file), first);
});

test("a stored upload releases its key, so picking the file again is a new attempt", () => {
  const file = { name: "截图.png", size: 2048, lastModified: 1700000000000, type: "image/png" };
  const first = uploadKeyFor("conv-a", file);
  clearUploadKey("conv-a", file);
  assert.notEqual(uploadKeyFor("conv-a", file), first);
});

test("reset drops cached upload keys too", () => {
  const file = { name: "截图.png", size: 2048, lastModified: 1700000000000, type: "image/png" };
  const before = uploadKeyFor("conv-a", file);
  reset();
  assert.notEqual(uploadKeyFor("conv-a", file), before);
});
