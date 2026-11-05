"""Transcoding provider protocol: transcode(asset, output_prefix) -> (master_key, renditions).

Providers run outside HTTP requests, must finish uploading every output before
returning, and must keep outputs under the supplied, attempt-specific prefix.
The queue worker owns retries, leases and the ready transition.
"""
import json
import math
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path

from django.conf import settings

from common.multipart_upload import _client


class TranscodingError(Exception):
    """A safe failure category; never include tool stderr or storage URLs."""


def rendition_ladder(width, height, sample_aspect_ratio='1:1', rotation=0):
    """Fit square-pixel display dimensions inside source pixels and 1920x1080.

    Anamorphic inputs describe display width using pixel aspect ratio. Merely
    forcing SAR=1 after scaling stored dimensions squeezes or stretches them.
    Fit that display ratio into the original pixel bounds before choosing rungs.
    FFmpeg autorotates on decode, so quarter turns also invert the display ratio.
    """
    if width < 2 or height < 2 or width > 16384 or height > 16384:
        raise TranscodingError('Unsupported video dimensions.')
    try:
        pixel_aspect = Fraction(str(sample_aspect_ratio).replace(':', '/'))
        if pixel_aspect <= 0:
            pixel_aspect = Fraction(1)
    except (ValueError, ZeroDivisionError):
        pixel_aspect = Fraction(1)
    display_aspect = Fraction(width, height) * pixel_aspect
    rotation = round(float(rotation)) % 360
    if rotation not in (0, 90, 180, 270):
        raise TranscodingError('Unsupported video dimensions.')
    if rotation in (90, 270):
        width, height = height, width
        display_aspect = 1 / display_aspect
    max_width, max_height = min(width, 1920), min(height, 1080)
    if Fraction(max_width, max_height) > display_aspect:
        max_width = int(max_height * display_aspect)
    else:
        max_height = int(max_width / display_aspect)
    if max_width < 2 or max_height < 2:
        raise TranscodingError('Unsupported video dimensions.')
    heights = [value for value in (360, 480, 720, 1080) if value <= max_height]
    if not heights:
        heights = [max_height - max_height % 2]
    bitrates = ((360, 800000), (480, 1400000), (720, 2800000), (1080, 5000000))
    result = []
    for output_height in heights:
        output_width = max(2, int(display_aspect * output_height) // 2 * 2)
        bitrate = next(rate for bound, rate in bitrates if output_height <= bound)
        result.append({'width': output_width, 'height': output_height, 'bitrate': bitrate})
    return result


class FFmpegProvider:
    """CPU H.264/AAC VOD HLS; source and output bytes move directly through S3."""

    def _run(self, command, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TranscodingError('Transcoding timed out.')
        try:
            return subprocess.run(
                command, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=remaining,
            ).stdout
        except subprocess.TimeoutExpired as exc:
            raise TranscodingError('Transcoding timed out.') from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise TranscodingError('Media could not be decoded or encoded.') from exc

    def transcode(self, asset, output_prefix):
        expected_prefix = f'protected/hls/{asset.pk}/'
        if not output_prefix.startswith(expected_prefix) or '..' in output_prefix or not output_prefix.endswith('/'):
            raise TranscodingError('Invalid output prefix.')
        deadline = time.monotonic() + max(1, int(getattr(settings, 'MEDIA_PROCESSING_TIMEOUT', 14400)))
        config = settings.MEDIA_STORAGE
        bucket = config['S3_BUCKET_NAME']
        client = _client(config)
        with tempfile.TemporaryDirectory(prefix='cbmtv-transcode-', dir=getattr(settings, 'MEDIA_TEMP_ROOT', None)) as directory:
            root = Path(directory)
            source = root / 'source'
            output = root / 'output'
            output.mkdir()
            try:
                response = client.get_object(Bucket=bucket, Key=asset.source_key)
                body = response['Body']
                try:
                    if response.get('ContentLength') != asset.size_bytes:
                        raise TranscodingError('Source size does not match the completed upload.')
                    written = 0
                    with source.open('wb') as destination:
                        while chunk := body.read(8 * 1024 * 1024):
                            if time.monotonic() >= deadline:
                                raise TranscodingError('Transcoding timed out.')
                            written += len(chunk)
                            if written > asset.size_bytes:
                                raise TranscodingError('Source size does not match the completed upload.')
                            destination.write(chunk)
                    if written != asset.size_bytes:
                        raise TranscodingError('Source download was incomplete.')
                finally:
                    body.close()
            except TranscodingError:
                raise
            except Exception as exc:
                raise TranscodingError('Source could not be downloaded.') from exc

            # Restrict both protocols and demuxers: an uploaded playlist must
            # never turn the worker into a remote URL/local-file fetch service.
            input_options = ['-protocol_whitelist', 'file,pipe,crypto', '-format_whitelist', 'mov,matroska,webm']
            probe_data = self._run([
                getattr(settings, 'MEDIA_FFPROBE_BIN', 'ffprobe'), '-v', 'error',
                *input_options, '-show_streams', '-show_format', '-of', 'json', str(source),
            ], deadline)
            try:
                streams = json.loads(probe_data)['streams']
                video = next(stream for stream in streams if stream.get('codec_type') == 'video' and not stream.get('disposition', {}).get('attached_pic'))
                width, height = int(video['width']), int(video['height'])
                rotations = [entry.get('rotation', 0) for entry in video.get('side_data_list', [])]
                rotation = float(rotations[0] if rotations else video.get('tags', {}).get('rotate', 0))
                rate = float(Fraction(video.get('avg_frame_rate') or video.get('r_frame_rate') or '30'))
                if not math.isfinite(rate) or rate <= 0:
                    rate = 30
                rate = min(rate, 30)
            except (ValueError, TypeError, KeyError, StopIteration, ZeroDivisionError) as exc:
                raise TranscodingError('Source does not contain a supported video stream.') from exc
            audio = any(stream.get('codec_type') == 'audio' for stream in streams)
            renditions = rendition_ladder(width, height, video.get('sample_aspect_ratio', '1:1'), rotation)
            try:
                duration = float(json.loads(probe_data).get('format', {}).get('duration') or video.get('duration', 0))
                if math.isfinite(duration) and duration > 0:
                    for rendition in renditions:
                        rendition['duration_seconds'] = duration
            except (TypeError, ValueError):
                pass
            master = ['#EXTM3U', '#EXT-X-VERSION:3', '#EXT-X-INDEPENDENT-SEGMENTS']
            for rendition in renditions:
                label = f"{rendition['height']}p"
                variant_directory = output / label
                variant_directory.mkdir()
                bitrate = rendition['bitrate']
                maxrate = int(bitrate * 1.1)
                command = [
                    getattr(settings, 'MEDIA_FFMPEG_BIN', 'ffmpeg'), '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                    *input_options, '-i', str(source), '-map', f"0:{video['index']}",
                ]
                if audio:
                    command += ['-map', '0:a:0', '-c:a', 'aac', '-profile:a', 'aac_low', '-b:a', '128k', '-ac', '2', '-ar', '48000']
                else:
                    command += ['-an']
                command += [
                    '-vf', f"scale={rendition['width']}:{rendition['height']},setsar=1",
                    '-r', str(rate), '-c:v', 'libx264', '-profile:v', 'main', '-level:v', '4.1',
                    '-pix_fmt', 'yuv420p', '-preset', 'medium', '-b:v', str(bitrate),
                    '-maxrate', str(maxrate), '-bufsize', str(bitrate * 2),
                    '-threads', str(max(1, min(8, int(getattr(settings, 'MEDIA_FFMPEG_THREADS', 2))))),
                    '-g', str(max(1, math.ceil(rate * 6))), '-keyint_min', str(max(1, math.ceil(rate * 6))),
                    '-sc_threshold', '0', '-force_key_frames', 'expr:gte(t,n_forced*6)',
                    '-f', 'hls', '-hls_time', '6', '-hls_list_size', '0', '-hls_playlist_type', 'vod',
                    '-hls_segment_type', 'mpegts', '-hls_flags', 'independent_segments',
                    '-hls_segment_filename', str(variant_directory / 'segment_%06d.ts'),
                    str(variant_directory / 'index.m3u8'),
                ]
                self._run(command, deadline)
                codecs = 'avc1.4d4029' + (',mp4a.40.2' if audio else '')
                bandwidth = math.ceil((maxrate + (128000 if audio else 0)) * 1.1)
                rendition.update({'bandwidth': bandwidth, 'codecs': codecs, 'playlist': f'{label}/index.m3u8'})
                master += [
                    f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={rendition["width"]}x{rendition["height"]},CODECS="{codecs}"',
                    rendition['playlist'],
                ]
            (output / 'master.m3u8').write_text('\n'.join(master) + '\n', encoding='utf-8')
            # The attempt namespace is immutable. Publish its master last, and
            # let the worker make it discoverable only after every upload succeeds.
            files = sorted(output.rglob('*.ts')) + sorted(path for path in output.rglob('*.m3u8') if path.name != 'master.m3u8') + [output / 'master.m3u8']
            try:
                for path in files:
                    if time.monotonic() >= deadline:
                        raise TranscodingError('Transcoding timed out.')
                    client.upload_file(str(path), bucket, output_prefix + path.relative_to(output).as_posix(), ExtraArgs={
                        'ContentType': 'application/vnd.apple.mpegurl' if path.suffix == '.m3u8' else 'video/mp2t',
                        'CacheControl': 'public,max-age=31536000,immutable',
                    })
            except TranscodingError:
                raise
            except Exception as exc:
                raise TranscodingError('Encoded media could not be uploaded.') from exc
        return output_prefix + 'master.m3u8', renditions
