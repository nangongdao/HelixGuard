/* H01 widget side: the pure halves of "resumable send" and "idle polling".
 *
 * `widget-app.js` is DOM-bound and is kept thin on purpose (it is an entry
 * point with a 500-line ceiling), so the decisions that must not be wrong —
 * whether a retry is the same send attempt, whether a poll may run, how far the
 * cursor may move — live here as pure functions and are tested without a
 * browser.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  advanceCursor,
  conversationSignals,
  mergePolledMessages,
  nextPollDelayMs,
  nextSendAttempt,
  shouldPoll,
} from "../../app/static/js/widget-core.js";

const ids = (...values) => {
  let index = 0;
  return () => values[index++] ?? `generated-${index}`;
};

test("a first send attempt mints a fresh channel id", () => {
  const attempt = nextSendAttempt(null, "  where is my order?  ", ids("id-1"));
  assert.equal(attempt.channelMessageId, "id-1");
  assert.equal(attempt.content, "where is my order?");
});

test("retrying the same text reuses the channel id so the server sees a replay", () => {
  const first = nextSendAttempt(null, "where is my order?", ids("id-1"));
  const retry = nextSendAttempt(first, "where is my order?", ids("id-2"));
  assert.equal(retry.channelMessageId, "id-1");
});

test("edited text is a new message, not a replay of the failed one", () => {
  const first = nextSendAttempt(null, "where is my order?", ids("id-1"));
  const edited = nextSendAttempt(first, "where is my refund?", ids("id-2"));
  assert.equal(edited.channelMessageId, "id-2");
  assert.equal(edited.content, "where is my refund?");
});

test("a blank draft never becomes an attempt", () => {
  assert.equal(nextSendAttempt(null, "   ", ids("id-1")), null);
  assert.equal(nextSendAttempt({ channelMessageId: "id-1", content: "x" }, "", ids("id-2")), null);
});

test("polling merges the delta without resurrecting optimistic bubbles", () => {
  const merged = mergePolledMessages(
    [
      { id: "local-web-1", role: "customer", content: "optimistic", created_at: "2026-01-01T00:00:02Z" },
      { id: "msg-1", role: "customer", content: "confirmed", created_at: "2026-01-01T00:00:01Z" },
    ],
    [
      { id: "msg-1", role: "customer", content: "confirmed", created_at: "2026-01-01T00:00:01Z" },
      { id: "msg-2", role: "operator", content: "答复", created_at: "2026-01-01T00:00:03Z" },
      { id: "note-1", role: "internal_note", content: "内部" },
    ],
  );
  assert.deepEqual(merged.map((message) => message.id), ["msg-1", "msg-2"]);
});

test("conversation status drives the handoff and CSAT banners", () => {
  assert.deepEqual(conversationSignals("waiting_human", ""), {
    handoff: true,
    resolved: false,
    resolvedSurveyUrl: "",
  });
  assert.equal(conversationSignals("human_active", "").handoff, true);
  assert.equal(conversationSignals("open", "").handoff, false);
  assert.deepEqual(conversationSignals("resolved", "/api/qa-spot-check/tok"), {
    handoff: false,
    resolved: true,
    resolvedSurveyUrl: "/api/qa-spot-check/tok",
  });
  // Resolved without a survey link must not show a dead rating control.
  assert.equal(conversationSignals("resolved", "").resolved, false);
});

test("polling stops for resolved, busy and hidden conversations", () => {
  const base = { conversationId: "conv-1", resolved: false, busy: false, hidden: false };
  assert.equal(shouldPoll(base), true);
  assert.equal(shouldPoll({ ...base, conversationId: null }), false);
  assert.equal(shouldPoll({ ...base, resolved: true }), false);
  assert.equal(shouldPoll({ ...base, busy: true }), false);
  assert.equal(shouldPoll({ ...base, hidden: true }), false);
  assert.equal(shouldPoll(), false);
});

test("poll backoff grows and then stops at the ceiling", () => {
  assert.equal(nextPollDelayMs(0), 4000);
  assert.equal(nextPollDelayMs(1), 8000);
  assert.equal(nextPollDelayMs(2), 16000);
  assert.equal(nextPollDelayMs(3), 30000);
  assert.equal(nextPollDelayMs(99), 30000);
  assert.equal(nextPollDelayMs("nonsense"), 4000);
});

test("the cursor only ever moves on a confirmed page", () => {
  assert.equal(advanceCursor("", "next-1"), "next-1");
  assert.equal(advanceCursor("next-1", "next-2"), "next-2");
  // A failed or empty page must leave the cursor where it was, so the next
  // poll re-reads the same window instead of skipping it.
  assert.equal(advanceCursor("next-1", ""), "next-1");
  assert.equal(advanceCursor("next-1", undefined), "next-1");
  assert.equal(advanceCursor(undefined, ""), "");
});
