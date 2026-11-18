"""Offline behavior tests: no platform requests, package installs or model downloads."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/youtube-bilibili-digest/scripts/fetch_transcript.py'
spec = importlib.util.spec_from_file_location('fetch_transcript', SCRIPT)
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)


class CaptionTests(unittest.TestCase):
    def test_json3_milliseconds_and_text(self):
        result = f.parse_caption(json.dumps({'events': [
            {'tStartMs': 2351, 'dDurationMs': 1138, 'segs': [{'utf8': 'A &amp; '}, {'utf8': 'B'}]},
            {'tStartMs': 0}
        ]}))
        self.assertEqual(result, [{'start': 2.351, 'end': 3.489, 'text': 'A & B'}])

    def test_bilibili_json(self):
        self.assertEqual(f.parse_caption('{"body":[{"from":1.25,"to":2.5,"content":"你好"}]}'),
                         [{'start': 1.25, 'end': 2.5, 'text': '你好'}])

    def test_xml(self):
        self.assertEqual(f.parse_caption('<transcript><text start="4.25" dur="2">A &amp; B</text></transcript>'),
                         [{'start': 4.25, 'end': 6.25, 'text': 'A & B'}])

    def test_vtt(self):
        cues = f.parse_caption('WEBVTT\n\n00:01.250 --> 00:03.500 align:start\n<b>Hello</b>\nworld\n\n')
        self.assertEqual(cues, [{'start': 1.25, 'end': 3.5, 'text': 'Hello world'}])

    def test_srt_hour_and_comma(self):
        cues = f.parse_caption('1\n01:02:03,125 --> 01:02:04,000\nLater\n\n')
        self.assertEqual(cues[0]['start'], 3723.125)

    def test_empty_and_malformed(self):
        for text in ('', 'not captions', '{broken', '<transcript><text>'):
            self.assertEqual(f.parse_caption(text), [])

    def test_sort_and_preserve_distinct_late_repeat(self):
        cues = f.normalize_segments([
            {'start': 10, 'end': 11, 'text': 'Hello'},
            {'start': 1, 'end': 2, 'text': 'Hello'},
            {'start': 1, 'end': 2, 'text': 'Hello'},
            {'start': 4, 'end': 5, 'text': '   '}
        ])
        self.assertEqual([c['start'] for c in cues], [1, 10])

    def test_nearby_progressive_cues_keep_actual_start(self):
        cues = f.normalize_segments([
            {'start': 1, 'end': 2, 'text': 'Hello'},
            {'start': 1.5, 'end': 3, 'text': 'Hello world'}
        ])
        self.assertEqual(cues, [{'start': 1.5, 'end': 3, 'text': 'Hello world'}])

    def test_track_language_then_manual_tiebreak(self):
        tracks = [{'languageCode': 'en', 'automatic': False},
                  {'languageCode': 'zh-Hans', 'automatic': True},
                  {'languageCode': 'zh-Hans', 'automatic': False}]
        self.assertIs(f.choose_track(tracks), tracks[2])


class RoutingTests(unittest.TestCase):
    def test_supported_youtube_forms(self):
        for url in ('https://youtu.be/abcdefghijk', 'https://www.youtube.com/watch?v=abcdefghijk',
                    'https://youtube.com/shorts/abcdefghijk', 'https://youtube.com/live/abcdefghijk',
                    'https://youtube.com/embed/abcdefghijk'):
            self.assertEqual(f.youtube_id(url), 'abcdefghijk')

    def test_reject_unsupported_hosts_before_network(self):
        with patch.object(f, 'request_text') as request:
            for url in ('https://x.com/user/status/123', 'https://youtube.com.evil.test/watch?v=abcdef',
                        'file:///tmp/video', 'https://example.com/video'):
                with self.assertRaises(f.FetchError):
                    f.fetch(url)
            request.assert_not_called()

    def test_direct_success_skips_fallbacks(self):
        direct = {'segments': [{'start': 1, 'end': 2, 'text': 'ok'}]}
        with patch.object(f, 'fetch_youtube_direct', return_value=direct), \
             patch.object(f, 'fetch_with_yt_dlp_python') as fallback, \
             patch.object(f, 'fetch_audio_and_transcribe') as audio:
            self.assertIs(f.fetch('https://youtu.be/abcdefghijk'), direct)
            fallback.assert_not_called()
            audio.assert_not_called()

    def test_audio_follows_both_caption_fallbacks(self):
        events = []
        def stage(name, result):
            def invoke(*args):
                events.append(name)
                return result
            return invoke
        audio = {'segments': [{'start': 2, 'end': 3, 'text': 'local'}], 'transcriptSource': 'local-whisper'}
        with patch.object(f, 'fetch_youtube_direct', side_effect=stage('direct', {'segments': []})), \
             patch.object(f, 'fetch_with_yt_dlp_python', side_effect=stage('python', None)), \
             patch.object(f, 'fetch_with_yt_dlp_cli', side_effect=stage('cli', None)), \
             patch.object(f, 'fetch_audio_and_transcribe', side_effect=stage('audio', audio)):
            self.assertIs(f.fetch('https://youtu.be/abcdefghijk'), audio)
        self.assertEqual(events, ['direct', 'python', 'cli', 'audio'])

    def test_total_failure_preserves_metadata_and_action(self):
        with patch.object(f, 'fetch_youtube_direct', return_value={'segments': [], 'title': 'Keep title'}), \
             patch.object(f, 'fetch_with_yt_dlp_python', return_value=None), \
             patch.object(f, 'fetch_with_yt_dlp_cli', return_value=None), \
             patch.object(f, 'fetch_audio_and_transcribe', side_effect=f.FetchError('missing ffmpeg')):
            result = f.fetch('https://youtu.be/abcdefghijk')
        self.assertEqual(result['title'], 'Keep title')
        self.assertEqual(result['diagnostic'], 'missing ffmpeg')
        self.assertIn('FFmpeg', result['nextStep'])
        self.assertNotIn('不默认下载音频', result['nextStep'])

    def test_bilibili_selected_part_uses_its_cid(self):
        calls = []
        def request(url, **kwargs):
            calls.append(url)
            if '/view?' in url:
                return json.dumps({'data': {'bvid': 'BV1234567890', 'title': 'Parts',
                    'pages': [{'cid': 11, 'duration': 10}, {'cid': 22, 'duration': 20}]}})
            if '/player/' in url:
                return json.dumps({'data': {'subtitle': {'subtitles': [
                    {'lan': 'zh-CN', 'subtitle_url': '//captions.test/part2'}]}}})
            return '{"body":[{"from":1,"to":2,"content":"第二部分"}]}'
        with patch.object(f, 'request_text', side_effect=request):
            result = f.fetch_bilibili_direct('https://www.bilibili.com/video/BV1234567890?p=2')
        self.assertIn('cid=22', calls[1])
        self.assertTrue(result['url'].endswith('?p=2'))
        self.assertEqual(result['duration'], 20)
        self.assertEqual(result['segments'][0]['text'], '第二部分')

    def test_bilibili_out_of_range_is_not_silently_clamped(self):
        response = json.dumps({'data': {'bvid': 'BV1234567890', 'pages': [{'cid': 11}]}})
        with patch.object(f, 'request_text', return_value=response), \
             patch.object(f, 'fetch_with_yt_dlp_python') as fallback:
            with self.assertRaisesRegex(f.FetchError, '超出范围'):
                f.fetch('https://www.bilibili.com/video/BV1234567890?p=9')
            fallback.assert_not_called()


class AudioAndCliTests(unittest.TestCase):
    def test_temporary_audio_is_removed_after_success_and_failure(self):
        for fail in (False, True):
            paths = []
            def run(command, **kwargs):
                output = Path(command[command.index('-o') + 1])
                audio = output.parent / 'audio.wav'
                audio.write_bytes(b'fake test audio')
                paths.append(audio)
                return subprocess.CompletedProcess(command, 0, stdout='{"title":"Test"}', stderr='')
            def transcribe(audio, out, platform):
                self.assertTrue(audio.exists())
                if fail:
                    raise f.FetchError('transcription failed')
                return [{'start': 1, 'end': 2, 'text': 'ok'}]
            with patch.object(f.shutil, 'which', side_effect=lambda name: '/mock/uvx' if name == 'uvx' else None), \
                 patch.object(f.subprocess, 'run', side_effect=run), \
                 patch.object(f, 'transcribe_audio', side_effect=transcribe):
                if fail:
                    with self.assertRaises(f.FetchError):
                        f.fetch_audio_and_transcribe('https://youtu.be/abcdefghijk', {'platform': 'youtube'})
                else:
                    result = f.fetch_audio_and_transcribe('https://youtu.be/abcdefghijk', {'platform': 'youtube'})
                    self.assertEqual(result['transcriptSource'], 'local-whisper')
            self.assertFalse(paths[0].exists())
            self.assertFalse(paths[0].parent.exists())

    def test_cli_failure_is_json_and_exit_two(self):
        result = subprocess.run([sys.executable, str(SCRIPT), 'https://x.com/user/status/123'],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['segments'], [])

    def test_cli_json_is_utf8_even_with_legacy_console_encoding(self):
        result = subprocess.run([sys.executable, str(SCRIPT), 'https://example.com/'],
                                env={**os.environ, 'PYTHONIOENCODING': 'cp1252'},
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('请提供', json.loads(result.stdout)['error'])

    def test_cli_output_writes_json_and_prints_path(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out.json'
            result = subprocess.run([sys.executable, str(SCRIPT), 'https://example.com/', '--output', str(output)],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout.strip(), str(output))
            self.assertIn('error', json.loads(output.read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
