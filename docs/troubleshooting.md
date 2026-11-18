# Troubleshooting / 故障排查

Read the JSON even when the command exits with code `2`. With `--output`, read the destination file; stdout contains only its path.

| Symptom / 现象 | Cause and next step / 原因与下一步 |
| --- | --- |
| Unsupported-link error | Use a single YouTube/Bilibili HTTP(S) link. X links require separately finding and verifying an equivalent upload. 不支持把 X 链接直接传给脚本。 |
| No public captions | Some videos have none, expose only burned-in captions, or require authentication. Try the optional local fallback; otherwise provide a transcript. 硬字幕不等于可读取的公开字幕。 |
| `uvx` unavailable | Install uv if you want the CLI fallback. The direct caption path still works without it; an importable yt-dlp package can also retrieve captions. |
| Audio extraction fails | Check FFmpeg and current yt-dlp/platform requirements. A restricted video may remain inaccessible. This project does not read cookies or bypass account restrictions. |
| MLX fails on Windows, Linux or Intel Mac | The bundled local speech-recognition path targets Apple Silicon. Use public captions or provide a transcript on other systems. |
| First transcription is slow | Python packages and model weights may download on first use. Confirm network access and available storage. Model caches can remain after temporary audio is deleted. |
| YouTube asks for JavaScript support | Provide a currently supported runtime such as Node. The CLI path adds `--js-runtimes node` when Node exists. Additional upstream requirements may still apply. |
| Bilibili selected part is wrong | Keep `?p=N`; out-of-range parts are rejected. `b23.tv` short links depend on yt-dlp behavior. Report the sanitized URL and expected part if the fallback selects incorrectly. |
| Summary timestamp disagrees with X | Edited uploads and advertisements may differ. The timestamp belongs to the transcribed YouTube/Bilibili upload, not automatically to X. |
| Misheard names or figures | Automatic captions and Whisper are imperfect. Verify high-stakes quotes in the source; don't silently strengthen uncertain claims. |

## Change the transcription model

The default is `mlx-community/whisper-large-v3-turbo`. Set another compatible MLX Whisper model for one invocation:

```bash
VIDEO_DIGEST_WHISPER_MODEL="mlx-community/whisper-large-v3-turbo" \
  python3 skills/youtube-bilibili-digest/scripts/fetch_transcript.py "<video-url>"
```

Model quality, licensing, resource requirements and availability are determined by the selected model. The script allows 10 minutes for audio extraction and 30 minutes for local transcription; slow machines or long recordings may hit those limits.

## Report a problem

Use the [bug report form](https://github.com/xuxinmaxen/youtube-bilibili-digest/issues/new?template=bug_report.yml). Include OS/CPU, Python version, the failing stage and sanitized error text. Do not post private URLs, full copyrighted transcripts, cookies, credentials or local file paths containing sensitive information.
