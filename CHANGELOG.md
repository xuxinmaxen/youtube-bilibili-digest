# Changelog

## 1.0.0 — 2026-11-17

First public release of the existing local skill.

- Public-caption retrieval for YouTube and Bilibili, alternate yt-dlp extraction and optional Apple Silicon MLX transcription.
- Portable skill instructions, explicit dependency requirements and accurate temporary-file/cache notes.
- Ensure UTF-8 CLI JSON output on Windows, including redirected output.
- Corrected a stale failure message that contradicted the audio transcription fallback.
- Reject out-of-range Bilibili parts instead of silently selecting the last part.
- Documented the agent-assisted X-to-YouTube discovery workaround without claiming script support for X.
- Added bilingual documentation, an attributed interview example, workflow illustration and designed output previews.
- Added offline behavior tests and cross-platform Python CI.
