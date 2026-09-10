# Development log

## 2026-09-11 (KST) — Preserve UNKNOWN_BINARY evidence

- Report: an unrecognized response had signature `46696e616e636961`
  (`Financia`). `pipeline.py` logged only eight bytes and immediately reported
  an unsupported format, losing the evidence needed to diagnose the response.
- Change: log the first 512 bytes as hex, UTF-8 and latin-1, plus response
  metadata, redirect history, total byte count, SHA-256 and decoder exception
  type. Preserve all response.content bytes in a unique temporary .bin by
  default and include its path in the error. Saving can be disabled or directed
  to a chosen directory. See [usage](unknown-binary-diagnostics.md).
- Environment: system Python lacked BeautifulSoup (`ModuleNotFoundError:
  No module named 'bs4'`). Created an ignored `venv` and installed the
  requests/BeautifulSoup/Pillow versions pinned in requirements.txt for testing.
- Initial command: `venv\Scripts\python.exe -m unittest discover -s tests -v`.
  Five cases failed during fixture cleanup with `PermissionError: [WinError 32]`
  on the temporary registry.db. SQLite connection context managers do not close
  connections, and retained exception tracebacks kept the connections alive.
  The fixture now tracks and explicitly closes its own DB connections before
  temporary-directory cleanup; production DB behavior is outside this change.
- Verification: the same command passes all 6 tests, covering exact 512-byte
  previews, full-body preservation, text starting with Financia, invalid/split
  UTF-8, empty input, unique filenames, default temp location, disabled saving,
  filesystem failure, and unchanged PNG/PDF/ZIP/EPUB/DjVu download paths.
  `git diff --check` passes. Tests use generated payloads and isolated SQLite
  files; no Google Sheets/Drive data is modified.
- Limitation: the original failing URL/full response was not supplied, so its
  upstream cause remains undetermined. Reprocessing it with this version will
  produce the evidence needed for diagnosis.
- Commit setup: `git commit` initially reported `Author identity unknown`.
  Used per-command agent identity `Codex <codex@localhost>` for this commit;
  global Git settings and the user's identity were not changed.

## 2026-09-11 00:30 KST — Resolve intermediate pages and mirrors

- Confirmed the old resolver inspected only .pdf links on one HTML page and
  accepted only an immediately returned PDF. It had no MD5 identity validation
  or traversal of intermediate/mirror pages. The queue treated description
  pages as image pages and could select a cover instead of the requested file.
- Added bounded traversal of advertised file/mirror links, fallback after
  HTTP/unknown-body/checksum failures, explicit MD5 validation, resolution logs,
  and UI inputs/results. File-mode queue rows retain the original URL for the
  download worker. Existing image rows keep their previous behavior.
- Each HTTP download redirect is checked before fetching; explicit caller
  cookies are not sent to another origin. No site-specific endpoints are guessed.
- First resolver test run: 16 passed, two redirect fixtures failed at
  `Response.close()` because their fabricated responses had no raw stream and
  did not mark their supplied body consumed. Marked fixture content consumed,
  matching the non-streaming requests responses represented by these tests.
- Validation: `venv\Scripts\python.exe -m unittest discover -s tests -v`
  passes all 19 tests, including multihop fallback, MD5 mismatch, preserved
  source/final URLs, API/queue integration, image-row compatibility, cycles,
  traversal limits, and redirect address/cookie checks. `git diff --check` passes.
- Runtime verification: restarted the local server and confirmed the updated
  URL/MD5 controls in the browser, preserving the user's existing URL input.
  That input previously failed with a final-CDN read timeout. A failed direct
  endpoint cannot supply an alternative mirror list; the original description
  page is needed. No user URL, temporary key or response payload was added to Git.
- See [resolver behavior and limits](file-resolver.md). Generated fixtures
  establish behavior; the external description/mirror page and successful
  download have not been verified.

## 2026-09-11 — Recover from malformed links and expose saved files

- Runtime failures: both POST `/api/images/download` and `/api/links/extract`
  returned HTTP 500 with `ValueError: Invalid IPv6 URL`. A real description
  page contained an ed2k link with square brackets in its filename. urljoin
  attempted to parse that unsupported scheme before the link filter ran.
- Added shared HTTP(S) link validation before parsing candidates. Malformed
  authorities, invalid ports, unsupported schemes and bad base URLs no longer
  abort processing of the remaining valid links.
- Runtime resolver logs also showed a global Mirrors directory and a language
  switch being visited ahead of GET. Navigation/language links are excluded;
  direct file/GET links are ranked before other candidates, before the cap.
- The registry contained a successfully downloaded PDF, but the web list only
  showed its filename. Added `/api/files/<id>/download` and visible file-download
  links. The route serves the stored bytes without revisiting an expiring remote
  link and restricts resolved paths to the configured download directory.
- Validation: `venv\Scripts\python.exe -m unittest discover -s tests -v`
  passes all 23 tests. Includes ed2k/invalid-HTTP fixtures, link API regression,
  candidate ranking, stored-file responses and out-of-directory rejection.
  `git diff --check` passes. Live saved-file GET returned HTTP 200, attachment
  disposition, and the expected 838292 bytes with matching MD5.
- User URL query keys and actual response bodies are excluded from this log.
