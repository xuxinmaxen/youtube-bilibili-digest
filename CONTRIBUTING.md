# Contributing

Bug fixes, source compatibility improvements, documentation translations and short attributed examples are welcome. Please open an issue for a substantial new platform or transcription backend before building it.

## Local workflow

1. Fork and clone the repository. Python 3.10+ is sufficient for offline tests.
2. Change `skills/youtube-bilibili-digest/` for runtime or instruction changes.
3. Update both READMEs if user-facing behavior changes.
4. Run `python3 -m unittest discover -s tests -v` and `python3 -m compileall -q skills tests`.
5. Describe the problem, resulting behavior and validation in your pull request.

CI does not access live platforms, install MLX, or download media. Keep unit tests offline and use mocks for external services. When reporting a manual live test, identify the platform, date and result without claiming that one accessible video proves general availability.

## Evidence and visual examples

Use actual segment start times and brief attributed quotes. Keep speaker claims separate from analysis. Do not bundle a full third-party transcript, audio, video or private conversation. Clearly label designed output previews; never pass them off as application screenshots.

## Visuals

Editable sources are in `docs/visuals/`; checked-in images are in `assets/`. Keep labels accurate, check text bounds, include accessible titles/descriptions and inspect exports at the README's rendered width. Changes to the workflow diagram must match the executable fallback order.
