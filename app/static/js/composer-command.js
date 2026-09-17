/**
 * Helix Support — composer command/receipt contract (ROADMAP H02).
 *
 * The operator workspace fires several async commands at the same composer:
 * copilot suggestions, tone rewrites, knowledge lookups and the reply send
 * itself. Each one races the operator's own navigation and typing, and the
 * naive shape — read `selectedId`, await, then write the shared DOM — loses
 * data: A's slow suggestion lands in B's copilot bar, A's late send
 * confirmation clears the draft being typed in B, and a rewrite computed from
 * an older draft overwrites the text the operator has since typed.
 *
 * This module owns the two facts that decide whether a result may be applied:
 *
 * - the **command ticket**: the conversation the command was issued for plus a
 *   per-surface monotonic generation. `isCurrent` is false as soon as the
 *   operator switches conversation or a newer command of the same surface
 *   supersedes the ticket. This generalises the stale-response guard
 *   `conversation-detail.js` already uses for detail loads
 *   (`detailSequence` + `selectedId` compared at apply time).
 * - the **draft version**: a counter bumped on every operator keystroke, so a
 *   result computed from an older draft is recognisable and can be dropped
 *   instead of clobbering newer input.
 *
 * It also owns the client half of the **send receipt**: a stable idempotency
 * key per send attempt. Reusing the key for the same text is what makes a
 * retry after a lost response safe — the server resolves it to the message it
 * already stored (see `app/routers/conversations.py`). The key is dropped on a
 * confirmed success, so sending the same text again later is a genuinely new
 * attempt rather than a silent replay.
 *
 * The **upload receipt** follows the same shape for attachments: a retried
 * upload of the same file resolves to the row the server already stored, so a
 * lost response costs neither a duplicate nor a second quota charge.
 *
 * State is module-local on purpose: this is the one command/receipt contract
 * shared by the legacy modules and the React island, not a third global store.
 */

import { newIdempotencyKey } from "./format.js?v=1.4.0";

let ctx = null;

/** Inject the legacy app.js singletons (state only). */
export function configure(deps) {
  ctx = deps;
}

/** Per-surface monotonic command generations. */
const generations = new Map();
/** Per-conversation draft generations, bumped on every operator keystroke. */
const draftVersions = new Map();
/** In-flight send attempts: `${conversationId}\u0000${fingerprint}` → key. */
const sendKeys = new Map();
/** In-flight upload attempts, keyed the same way as sends. */
const uploadKeys = new Map();

function currentConversation() {
  return ctx?.state?.selectedId ?? null;
}

/** Open a command ticket for `surface` against `conversationId`. */
export function beginCommand(surface, conversationId) {
  const generation = (generations.get(surface) ?? 0) + 1;
  generations.set(surface, generation);
  const target = conversationId ?? currentConversation();
  return {
    surface,
    conversationId: target,
    generation,
    draftVersion: draftVersionFor(target),
  };
}

/**
 * True while the ticket is still the newest command of its surface *and* the
 * operator is still on the conversation it was issued for. Call this
 * immediately before applying a result.
 */
export function isCurrent(ticket) {
  if (!ticket) return false;
  return (
    ticket.conversationId === currentConversation() &&
    ticket.generation === (generations.get(ticket.surface) ?? 0)
  );
}

/** Current draft version for a conversation (0 when never typed into). */
export function draftVersionFor(conversationId) {
  return draftVersions.get(conversationId) ?? 0;
}

/** Record an operator edit, invalidating results computed from older text. */
export function bumpDraftVersion(conversationId) {
  if (!conversationId) return 0;
  const next = draftVersionFor(conversationId) + 1;
  draftVersions.set(conversationId, next);
  return next;
}

/**
 * True while the ticket is current *and* the draft it was computed from is
 * still the draft on screen. A result that fails this must be dropped, not
 * applied: the operator's newer text wins.
 */
export function isDraftUnchanged(ticket) {
  return isCurrent(ticket) && ticket.draftVersion === draftVersionFor(ticket.conversationId);
}

/** Forget all command/draft/receipt state (per-conversation teardown and test
 *  isolation). Superseding happens by bumping a ticket, so this is only for
 *  discarding the whole contract — never call it on a mere conversation
 *  switch, or an in-flight send's own confirmation stops being current. */
export function reset() {
  generations.clear();
  draftVersions.clear();
  sendKeys.clear();
  uploadKeys.clear();
}

function sendFingerprint(content, attachmentIds) {
  return `${content}\u0001${[...attachmentIds].sort().join(",")}`;
}

/**
 * The stable idempotency key for one send attempt.
 *
 * Same conversation, same content and same attachments → the same key, so a
 * retry resolves to the message the server already stored. Editing the text or
 * changing the attachment set yields a new key, so an edited reply can never
 * be swallowed as a replay of the earlier one.
 */
export function sendKeyFor(conversationId, content, attachmentIds = []) {
  const cacheKey = `${conversationId}\u0000${sendFingerprint(content, attachmentIds)}`;
  const existing = sendKeys.get(cacheKey);
  if (existing) return existing;
  const key = newIdempotencyKey();
  sendKeys.set(cacheKey, key);
  return key;
}

/** Drop the cached key once the server confirmed the attempt, so deliberately
 *  sending the same text again is a new attempt rather than a silent replay. */
export function clearSendKey(conversationId, content, attachmentIds = []) {
  sendKeys.delete(`${conversationId}\u0000${sendFingerprint(content, attachmentIds)}`);
}

/**
 * The stable identity of one picked file, derived from what the browser
 * exposes about it. Same file → same token, which is what lets a failed upload
 * stay addressable for a retry without holding on to a generated id.
 */
export function uploadTokenFor(file) {
  return [file?.name ?? "", file?.size ?? 0, file?.lastModified ?? 0, file?.type ?? ""].join("|");
}

/**
 * The stable idempotency key for one upload attempt.
 *
 * Same conversation and same file → the same key, so retrying after a lost
 * response resolves to the row the server already stored instead of storing
 * the bytes (and charging the tenant quota) twice. Picking a genuinely
 * different file yields a different key, so a corrected upload can never be
 * swallowed as a replay of the rejected one.
 */
export function uploadKeyFor(conversationId, file) {
  const cacheKey = `${conversationId}\u0000${uploadTokenFor(file)}`;
  const existing = uploadKeys.get(cacheKey);
  if (existing) return existing;
  const key = newIdempotencyKey();
  uploadKeys.set(cacheKey, key);
  return key;
}

/** Drop the cached upload key once the attachment is stored. */
export function clearUploadKey(conversationId, file) {
  uploadKeys.delete(`${conversationId}\u0000${uploadTokenFor(file)}`);
}

export default {
  configure,
  beginCommand,
  isCurrent,
  isDraftUnchanged,
  draftVersionFor,
  bumpDraftVersion,
  reset,
  sendKeyFor,
  clearSendKey,
  uploadTokenFor,
  uploadKeyFor,
  clearUploadKey,
};
