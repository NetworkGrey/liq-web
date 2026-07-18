# Wallet — project rules

Scope: this directory (`wallet/`) and the `/wallet` Flask route in `app.py`. Written at Stage 1 (first build: card capture + local storage). Read before touching anything here.

## The one rule that can't be relaxed

**Card images never leave the device.** No upload, no sync, no server storage, no fallback to server storage on any failure path. This page makes zero `fetch`/`XHR` calls of any kind — that's deliberate, not an oversight. If a future stage needs to send anything to a server (sync, backup, tagging against the KB), that's a product decision requiring an explicit, visible opt-in and a rewrite of the device-only notice text — not something to slip in as an implementation detail.

If you add any network call to this directory's code, you have broken the core promise this feature is sold on. Don't.

## Storage layer

- IndexedDB, database `liq_wallet`, object store `cards`, keyPath `id`.
- `DB_VERSION` is a real version number (currently `1`). Bump it and add an `onupgradeneeded` migration path when the record shape changes — never silently reinterpret old records under a new shape.
- Card record shape (documented here since this file has no build step or type checker to enforce it):
  ```
  {
    id: string,          // crypto.randomUUID() where available
    createdAt: string,   // ISO 8601
    image: Blob,         // image/jpeg, canvas re-exported — see metadata rule below
    width: number,       // px, of the stored (post-crop) image
    height: number,      // px
  }
  ```
- Every storage failure (open blocked/denied, private-mode restrictions, quota exceeded, transaction abort) must produce an explicit error state that says nothing was saved. Never let a failed write render as if it succeeded. Never silently drop an error.

## Metadata stripping is structural, not a checklist item

The captured `File`/`Blob` from the file input is **never** stored directly and never touches `saveCard()`. Every save path draws the (rotated, cropped) image onto a `<canvas>` and calls `toBlob()` — canvas pixel data has no EXIF channel, so the re-exported Blob is guaranteed metadata-free regardless of what the original file contained. This isn't a cleaning step bolted onto the flow; it's the only path that produces the Blob that gets saved. If you ever add a code path that calls `saveCard()` with anything other than a freshly-`toBlob()`'d canvas output, you've reintroduced the leak this was built to close.

## Stage boundaries

This build is Stage 1 only:

- **In scope:** front image capture, crop, rotate (90° increments), canvas re-render/metadata strip, save, list view reading "stored on this device" for every card.
- **Out of scope, don't add without a separate instruction:** back image, barcode crop, scan mode (Stage 2); tagging, held-programme linking, any distinction between cards in the list (Stage 3, deferred indefinitely as of this writing); reorder, edit, delete-all (Stage 4 polish). No Review/Join interaction. No iframe/embedding — this page is reached by a plain top-level link and must stay first-party (see below).

## Why this isn't iframed

`/wallet` is a direct, first-party, top-level page on the Railway origin — reached via a plain link, not embedded. This matters for storage reliability: iOS Safari's IndexedDB behavior in third-party/cross-origin iframe contexts is exactly what the disposable `/storage-test` spike (see repo root, delete once resolved) was built to check before committing to an architecture. Wallet storage depends on being first-party. Don't embed this page in an iframe on another origin without re-verifying that spike's findings still hold.

## Auth

No auth is enforced. The sign-in banner on the wallet page is inert — same disabled-button placeholder pattern already shipped on Review. Don't wire it to anything real without a separate instruction; it's cosmetic scaffolding for a beta, not a gate.

## Testing expectations

Any change here should keep these true, not just "the code runs":

- A network-intercept test confirming zero requests ever carry image bytes off-device. This is the core privacy promise — it must hold from Stage 1 onward, not be bolted on later.
- The storage-blocked error state actually renders when IndexedDB is unavailable (not just when the happy path is exercised).
- Add → crop/rotate → save → appears in list round-trips correctly.
- Metadata stripping is verified against a real photo with real EXIF data (GPS, camera make/model, orientation tag) — confirm the *output* bytes have no EXIF segment, not just that the canvas code path executed without throwing.
