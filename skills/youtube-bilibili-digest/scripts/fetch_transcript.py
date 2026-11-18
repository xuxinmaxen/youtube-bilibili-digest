#!/usr/bin/env python3
"""Fetch captions or locally transcribe audio from a YouTube/Bilibili URL."""

from __future__ import annotations

import argparse
import gzip
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
LANGUAGE_ORDER = ["zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "en", "en-US"]


class FetchError(RuntimeError):
    pass


class InvalidVideoPartError(FetchError):
    """Invalid selections must not be retried as a different video part."""


def request_text(url: str, *, referer: str | None = None) -> str:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            encoding = response.headers.get_content_charset() or "utf-8"
            return raw.decode(encoding, errors="replace")
    except Exception as exc:  # network failures vary by provider
        raise FetchError(f"请求字幕或视频页面失败：{exc}") from exc


def decode_escaped(value: str) -> str:
    try:
        return json.loads('"' + value + '"')
    except (json.JSONDecodeError, TypeError):
        return value


def clean_text(value: Any) -> str:
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("\u200b", "")
    return re.sub(r"\s+", " ", value).strip()


def parse_time(value: str | float | int) -> float:
    if isinstance(value, (float, int)):
        return float(value)
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, minutes, seconds = "0", parts[0], parts[1]
    else:
        return float(value)
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def normalize_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    last_key: tuple[float, str] | None = None
    for item in sorted(segments, key=lambda x: float(x.get("start", 0))):
        text = clean_text(item.get("text"))
        if not text:
            continue
        start = max(0.0, float(item.get("start", 0)))
        end = max(start, float(item.get("end", start + float(item.get("duration", 0)))))
        key = (round(start, 3), text)
        if key == last_key:
            continue
        current = {"start": round(start, 3), "end": round(end, 3), "text": text}
        if cleaned:
            previous = cleaned[-1]
            nearby = current["start"] <= previous["end"] + 0.15
            duplicate_or_progressive = (
                text == previous["text"]
                or text in previous["text"]
                or previous["text"] in text
            )
            if nearby and duplicate_or_progressive:
                if len(text) > len(previous["text"]) or (
                    len(text) == len(previous["text"]) and current["end"] > previous["end"]
                ):
                    cleaned[-1] = current
                    last_key = key
                continue
        cleaned.append(current)
        last_key = key
    return cleaned


def parse_json3(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    segments = []
    for event in data.get("events", []):
        if not event.get("segs"):
            continue
        start = float(event.get("tStartMs", 0)) / 1000
        duration = float(event.get("dDurationMs", 0)) / 1000
        value = "".join(str(seg.get("utf8") or "") for seg in event["segs"])
        segments.append({"start": start, "end": start + duration, "text": value})
    return normalize_segments(segments)


def parse_vtt_or_srt(text: str) -> list[dict[str, Any]]:
    text = text.replace("\ufeff", "")
    pattern = re.compile(
        r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)\s*-->\s*"
        r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)[^\n]*\n(?P<body>.*?)(?=\n\s*\n|\Z)",
        re.S,
    )
    segments = []
    for match in pattern.finditer(text):
        segments.append(
            {
                "start": parse_time(match.group("start")),
                "end": parse_time(match.group("end")),
                "text": re.sub(r"\n+", " ", match.group("body")),
            }
        )
    return normalize_segments(segments)


def parse_caption(text: str) -> list[dict[str, Any]]:
    stripped = text.lstrip()
    if stripped.startswith("{"):
        parsed = parse_json3(text)
        if parsed:
            return parsed
        try:
            data = json.loads(text)
            body = data.get("body") or data.get("subtitles") or []
            return normalize_segments(
                [{"start": x.get("from", 0), "end": x.get("to", x.get("from", 0)), "text": x.get("content", "")} for x in body]
            )
        except (TypeError, ValueError):
            pass
    if "<transcript" in stripped or stripped.startswith("<text"):
        try:
            root = ET.fromstring(stripped)
            return normalize_segments(
                [
                    {
                        "start": float(node.attrib.get("start", 0)),
                        "end": float(node.attrib.get("start", 0)) + float(node.attrib.get("dur", 0)),
                        "text": "".join(node.itertext()),
                    }
                    for node in root.findall(".//text")
                ]
            )
        except ET.ParseError:
            pass
    return parse_vtt_or_srt(text)


def choose_track(tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not tracks:
        return None

    def score(track: dict[str, Any]) -> tuple[int, int]:
        lang = str(track.get("languageCode") or track.get("lang") or track.get("lan") or "").lower()
        name = str(track.get("name") or track.get("lan_doc") or "").lower()
        for index, preferred in enumerate(LANGUAGE_ORDER):
            if lang == preferred.lower() or lang.startswith(preferred.lower() + "-"):
                return (len(LANGUAGE_ORDER) - index, 1 if not track.get("automatic") else 0)
        if "chinese" in name or "中文" in name:
            return (3, 1 if not track.get("automatic") else 0)
        if lang.startswith("en"):
            return (2, 1 if not track.get("automatic") else 0)
        return (0, 0)

    return max(tracks, key=score)


def extract_balanced(text: str, start: int) -> str | None:
    opening = text.find("[", start)
    if opening < 0:
        opening = text.find("{", start)
    if opening < 0:
        return None
    closing = "]" if text[opening] == "[" else "}"
    depth = 0
    quoted = False
    escaped = False
    for index in range(opening, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == text[opening]:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[opening : index + 1]
    return None


def youtube_id(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    if host in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/").split("/")[0] or None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        query_id = urllib.parse.parse_qs(parsed.query).get("v", [None])[0]
        if query_id:
            return query_id
        match = re.search(r"/(?:shorts|embed|live)/([\w-]{6,})", parsed.path)
        return match.group(1) if match else None
    return None


def bilibili_id(url: str) -> tuple[str, str] | None:
    match = re.search(r"(BV[0-9A-Za-z]{10})", url)
    if match:
        return ("bvid", match.group(1))
    match = re.search(r"(?:^|[/=])av(\d+)", url, re.I)
    if match:
        return ("aid", match.group(1))
    return None


def fetch_youtube_direct(url: str) -> dict[str, Any]:
    video_id = youtube_id(url)
    if not video_id:
        raise FetchError("不是可识别的 YouTube 视频链接")
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    page = request_text(canonical)
    title_match = re.search(r'"videoDetails":\{.*?"title":"(.*?)"', page, re.S)
    channel_match = re.search(r'"videoDetails":\{.*?"author":"(.*?)"', page, re.S)
    duration_match = re.search(r'"lengthSeconds":"(\d+)"', page)
    title = clean_text(decode_escaped(title_match.group(1))) if title_match else ""
    channel = clean_text(decode_escaped(channel_match.group(1))) if channel_match else ""
    duration = int(duration_match.group(1)) if duration_match else None
    tracks: list[dict[str, Any]] = []
    marker = page.find('"captionTracks"')
    if marker >= 0:
        raw = extract_balanced(page, marker)
        if raw:
            try:
                tracks = json.loads(raw)
            except json.JSONDecodeError:
                pass
    selected = choose_track(tracks)
    result = {"platform": "youtube", "url": canonical, "title": title, "channel": channel, "duration": duration, "segments": []}
    if not selected or not (selected.get("baseUrl") or selected.get("url")):
        return result
    caption_url = selected.get("baseUrl") or selected.get("url")
    query = urllib.parse.urlencode({"fmt": "json3"})
    caption_text = request_text(caption_url + ("&" if "?" in caption_url else "?") + query, referer=canonical)
    segments = parse_caption(caption_text)
    if not segments:
        segments = parse_caption(request_text(caption_url, referer=canonical))
    result.update({"transcriptSource": "captions" if segments else None, "captionLanguage": selected.get("languageCode"), "segments": segments})
    return result


def fetch_bilibili_direct(url: str) -> dict[str, Any]:
    identity = bilibili_id(url)
    if not identity:
        raise FetchError("不是可识别的 Bilibili BV/av 视频链接")
    key, value = identity
    api_url = "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({key: value})
    data = json.loads(request_text(api_url)).get("data") or {}
    if not data:
        raise FetchError("Bilibili 视频信息不可访问，可能需要登录或视频已失效")
    bvid = data.get("bvid") or value
    canonical = f"https://www.bilibili.com/video/{bvid}"
    pages = data.get("pages") or []
    try:
        requested_page = max(1, int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("p", [1])[0]))
    except (TypeError, ValueError):
        requested_page = 1
    if pages and requested_page > len(pages):
        raise InvalidVideoPartError(f"Bilibili 分 P 超出范围：请求第 {requested_page} P，视频共有 {len(pages)} P")
    page = pages[requested_page - 1] if pages else {}
    cid = page.get("cid") or data.get("cid")
    if requested_page > 1 and page:
        canonical = f"{canonical}?p={requested_page}"
    result: dict[str, Any] = {"platform": "bilibili", "url": canonical, "title": clean_text(data.get("title")), "channel": clean_text((data.get("owner") or {}).get("name")), "duration": page.get("duration") or data.get("duration"), "segments": []}
    if not cid:
        return result
    subtitle_data: dict[str, Any] = {}
    for endpoint in ("https://api.bilibili.com/x/player/wbi/v2", "https://api.bilibili.com/x/player/v2"):
        try:
            query = urllib.parse.urlencode({"bvid": bvid, "cid": cid})
            subtitle_data = json.loads(request_text(endpoint + "?" + query, referer=canonical)).get("data") or {}
            if (subtitle_data.get("subtitle") or {}).get("subtitles"):
                break
        except (FetchError, ValueError):
            continue
    tracks = []
    for item in ((subtitle_data.get("subtitle") or {}).get("subtitles") or []):
        subtitle_url = item.get("subtitle_url") or item.get("url")
        if subtitle_url:
            tracks.append({**item, "url": urllib.parse.urljoin("https:", subtitle_url)})
    selected = choose_track(tracks)
    if selected:
        result["segments"] = parse_caption(request_text(selected["url"], referer=canonical))
        result["captionLanguage"] = selected.get("lan") or selected.get("lan_doc")
    if result["segments"]:
        result["transcriptSource"] = "captions"
    return result


def _result_from_info(info: dict[str, Any], url: str, segments: list[dict[str, Any]], language: str | None = None) -> dict[str, Any]:
    return {
        "platform": "youtube" if youtube_id(url) else "bilibili",
        "url": info.get("webpage_url") or url,
        "title": clean_text(info.get("title")),
        "channel": clean_text(info.get("channel") or info.get("uploader")),
        "duration": info.get("duration"),
        "transcriptSource": "captions" if segments else None,
        "captionLanguage": language,
        "segments": segments,
    }


def fetch_with_yt_dlp_python(url: str) -> dict[str, Any] | None:
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return None
    options = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        tracks: list[dict[str, Any]] = []
        for automatic, key in ((False, "subtitles"), (True, "automatic_captions")):
            for lang, formats in (info.get(key) or {}).items():
                for fmt in formats or []:
                    if fmt.get("url"):
                        tracks.append({**fmt, "languageCode": lang, "automatic": automatic})
        selected = choose_track(tracks)
        segments = parse_caption(request_text(selected["url"], referer=url)) if selected else []
        return _result_from_info(info, url, segments, selected.get("languageCode") if selected else None)
    except Exception as exc:  # provider responses vary
        raise FetchError(f"yt-dlp 无法读取该视频：{exc}") from exc


def fetch_with_yt_dlp_cli(url: str) -> dict[str, Any] | None:
    """Use an ephemeral uvx yt-dlp install without downloading video/audio."""
    uvx = shutil.which("uvx")
    if not uvx:
        return None
    with tempfile.TemporaryDirectory(prefix="video-digest-") as temp_dir:
        output_template = str(Path(temp_dir) / "%(id)s.%(ext)s")
        command = [
            uvx, "--from", "yt-dlp", "yt-dlp", "--skip-download", "--no-playlist",
            "--write-subs", "--write-auto-subs", "--sub-langs", ",".join(LANGUAGE_ORDER),
            "--sub-format", "vtt", "--no-warnings", "--ignore-errors", "--print-json",
            "-o", output_template,
        ]
        if shutil.which("node"):
            command.extend(["--js-runtimes", "node"])
        command.append(url)
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FetchError(f"字幕抓取工具运行失败：{exc}") from exc
        info: dict[str, Any] = {}
        for line in reversed(completed.stdout.splitlines()):
            if line.lstrip().startswith("{"):
                try:
                    info = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        caption_files = list(Path(temp_dir).glob("*.vtt"))
        if not caption_files:
            return _result_from_info(info, url, [])

        def file_score(path: Path) -> tuple[int, int]:
            name = path.name.lower()
            auto = 0 if ".auto." not in name else -1
            for index, lang in enumerate(LANGUAGE_ORDER):
                if f".{lang.lower()}." in name:
                    return (len(LANGUAGE_ORDER) - index, auto)
            return (0, auto)

        selected_file = max(caption_files, key=file_score)
        segments = parse_caption(selected_file.read_text(encoding="utf-8", errors="replace"))
        parts = selected_file.name.split(".")
        language = parts[-2] if len(parts) > 2 else None
        result = _result_from_info(info, url, segments, language)
        if not segments and completed.stderr:
            result["toolMessage"] = completed.stderr[-800:].strip()
        return result


def _parse_last_json_line(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        if line.lstrip().startswith("{"):
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                continue
    return {}


def _whisper_command() -> list[str] | None:
    executable = shutil.which("mlx_whisper")
    if executable:
        return [executable]
    uvx = shutil.which("uvx")
    if uvx:
        return [uvx, "--from", "mlx-whisper", "mlx_whisper"]
    return None


def transcribe_audio(audio_path: Path, output_dir: Path, platform: str) -> list[dict[str, Any]]:
    command = _whisper_command()
    if not command:
        raise FetchError("本机没有可用的本地语音转文字工具（需要 mlx_whisper 或 uvx）")
    model = os.environ.get("VIDEO_DIGEST_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
    command.extend([
        "--model", model,
        "--output-format", "json",
        "--output-dir", str(output_dir),
        "--output-name", "transcript",
        "--verbose", "False",
    ])
    if platform == "bilibili":
        command.extend(["--language", "zh"])
    command.append(str(audio_path))
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FetchError(f"本地语音转文字运行失败：{exc}") from exc
    transcript_path = output_dir / "transcript.json"
    if completed.returncode != 0 or not transcript_path.exists():
        detail = (completed.stderr or completed.stdout or "").strip()[-1000:]
        raise FetchError(f"本地语音转文字失败：{detail or '没有生成转录结果'}")
    try:
        data = json.loads(transcript_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FetchError(f"读取本地转录结果失败：{exc}") from exc
    segments = []
    for item in data.get("segments", []):
        segments.append({"start": item.get("start", 0), "end": item.get("end", item.get("start", 0)), "text": item.get("text", "")})
    return normalize_segments(segments)


def fetch_audio_and_transcribe(url: str, fallback: dict[str, Any]) -> dict[str, Any] | None:
    uvx = shutil.which("uvx")
    if not uvx:
        raise FetchError("本机没有 uvx，无法自动提取视频音频")
    platform = fallback.get("platform") or ("youtube" if youtube_id(url) else "bilibili")
    with tempfile.TemporaryDirectory(prefix="video-digest-audio-") as temp_dir:
        temp_path = Path(temp_dir)
        output_template = str(temp_path / "%(id)s.%(ext)s")
        command = [
            uvx, "--from", "yt-dlp", "yt-dlp", "--no-playlist",
            "-f", "bestaudio/best", "-x", "--audio-format", "wav", "--audio-quality", "0",
            "--retries", "2", "--no-warnings", "--print-json", "-o", output_template,
        ]
        if shutil.which("node"):
            command.extend(["--js-runtimes", "node"])
        command.append(url)
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FetchError(f"提取视频音频失败：{exc}") from exc
        audio_files = list(temp_path.glob("*.wav"))
        if completed.returncode != 0 or not audio_files:
            detail = (completed.stderr or completed.stdout or "").strip()[-1000:]
            raise FetchError(f"提取视频音频失败：{detail or '没有生成音频文件'}")
        info = _parse_last_json_line(completed.stdout)
        if not info:
            info = {key: fallback.get(key) for key in ("title", "channel", "duration", "url")}
        transcript_dir = temp_path / "transcript"
        transcript_dir.mkdir()
        segments = transcribe_audio(audio_files[0], transcript_dir, platform)
        result = _result_from_info(info, url, segments, "zh" if platform == "bilibili" else None)
        result["transcriptSource"] = "local-whisper"
        result["transcriptionModel"] = os.environ.get("VIDEO_DIGEST_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
        result["transcriptionNote"] = "未找到公开字幕，已临时提取音频并在本机转录；临时音频已删除。"
        return result


def fetch(url: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    allowed_hosts = {
        "youtu.be", "www.youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
        "bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv", "www.b23.tv",
    }
    if parsed.scheme not in {"http", "https"} or host not in allowed_hosts:
        raise FetchError("请提供 youtube.com、youtu.be、bilibili.com 或 b23.tv 的 http(s) 链接")
    direct_error: str | None = None
    if youtube_id(url):
        try:
            direct = fetch_youtube_direct(url)
        except (FetchError, ValueError) as exc:
            direct_error = str(exc)
            direct = {"platform": "youtube", "url": url, "segments": []}
    elif bilibili_id(url):
        try:
            direct = fetch_bilibili_direct(url)
        except InvalidVideoPartError:
            raise
        except (FetchError, ValueError) as exc:
            direct_error = str(exc)
            direct = {"platform": "bilibili", "url": url, "segments": []}
    else:
        # b23.tv and other short links can only be resolved by yt-dlp.
        if re.search(r"https?://(?:www\.)?b23\.tv/", url):
            direct = {"platform": "bilibili", "url": url, "segments": []}
        else:
            raise FetchError("请提供 YouTube 或 Bilibili 视频链接")
    if direct.get("segments"):
        return direct
    errors = [direct_error] if direct_error else []
    for method in (fetch_with_yt_dlp_python, fetch_with_yt_dlp_cli):
        try:
            enhanced = method(url)
            if enhanced and enhanced.get("segments"):
                return enhanced
            if enhanced and not direct.get("title"):
                direct.update({k: v for k, v in enhanced.items() if v not in (None, "")})
        except FetchError as exc:
            errors.append(str(exc))
    try:
        transcribed = fetch_audio_and_transcribe(url, direct)
        if transcribed and transcribed.get("segments"):
            return transcribed
    except FetchError as exc:
        errors.append(str(exc))
    direct["error"] = "未找到可读取的公开字幕"
    direct["nextStep"] = "公开字幕与备用转录未取得可用文本。请检查 uvx、FFmpeg 和 Apple Silicon/MLX 环境，或提供字幕/文字稿。"
    if errors:
        direct["diagnostic"] = errors[-1]
    return direct


def main() -> int:
    # JSON is always UTF-8, including when stdout is redirected on Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Fetch public captions from YouTube or Bilibili")
    parser.add_argument("url")
    parser.add_argument("--output", help="Write JSON to a file instead of stdout")
    args = parser.parse_args()
    try:
        result = fetch(args.url.strip())
    except FetchError as exc:
        result = {"url": args.url.strip(), "segments": [], "error": str(exc)}
    except Exception as exc:
        result = {"url": args.url.strip(), "segments": [], "error": f"读取视频时发生错误：{exc}"}
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(payload)
    return 0 if result.get("segments") else 2


if __name__ == "__main__":
    raise SystemExit(main())
