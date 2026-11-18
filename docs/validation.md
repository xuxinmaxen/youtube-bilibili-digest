# Validation / 验证记录

Public release: **1.0.0**. Recorded on **2026-11-17**.

## Automated, offline

20 behavior tests run with Python's standard-library `unittest`:

- YouTube JSON3, Bilibili JSON, XML, VTT and SRT parsing.
- Millisecond/hour timestamps, text cleaning, ordering and nearby duplicate/progressive cues.
- Caption-language preference and manual-caption tie-breaking.
- Supported YouTube URL forms and rejection of unsupported hosts before network access.
- Direct-caption success short-circuit and ordered fallback behavior.
- Metadata preservation and actionable failure diagnostics.
- Selected Bilibili part/CID routing and out-of-range rejection without retrying another part.
- Temporary audio directory cleanup after both success and a handled transcription failure.
- CLI failure JSON/exit code, `--output` file behavior and UTF-8 output under a legacy console encoding.

CI runs those tests on Ubuntu, macOS and Windows with Python 3.10 and 3.12. It does not run MLX or contact live video platforms.

## Live smoke test

The released helper retrieved public English captions for [the official David Senra / Tobi Lütke interview](https://www.youtube.com/watch?v=ZSM2uFnJ5bs):

- Returned channel: David Senra.
- Returned duration: 8,621 seconds.
- Returned source: `captions`.
- Normalized segments: 2,946.

This confirms one accessible YouTube video's caption path at the time of testing. Bilibili and local MLX inference were not tested live for this release; their relevant routing and cleanup paths are covered by offline mocks. Platform restrictions, dependency changes and individual video settings can change outcomes.

## Visual checks

- README cover, designed summary preview and workflow rendered with a bundled Chromium runtime.
- SVG text checked against the canvas bounds.
- HTML sources checked at desktop width and 390px viewport width; wide figures use a local scroller.
- Workflow HTML passed the diagram skill's accessibility/single-file self-check.
- PNG exports visually inspected; vector workflow source and export included.
