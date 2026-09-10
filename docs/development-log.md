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
