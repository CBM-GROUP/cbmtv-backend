"""Owner-bound direct S3 uploads, durable completion and resumable part listing."""
import math
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.core import signing
from django.db import transaction
from content.models import MediaAsset
from .media_storage import MediaStorageError, build_delivery_url, safe_unique_filename, MEDIA_RULES

PART_SIZE = 8 * 1024 * 1024
SESSION_SALT = 'cbmtv-multipart-video-v1'


class MediaStorageUnavailable(MediaStorageError):
    status_code = 503


def _client(config):
    options = {'region_name': config['AWS_REGION'], 'config': Config(signature_version='s3v4')}
    if config.get('CREDENTIALS_MODE') == 'access_keys':
        if not config.get('AWS_ACCESS_KEY_ID') or not config.get('AWS_SECRET_ACCESS_KEY'):
            raise MediaStorageError('AWS media storage credentials are not configured.')
        options.update(aws_access_key_id=config['AWS_ACCESS_KEY_ID'], aws_secret_access_key=config['AWS_SECRET_ACCESS_KEY'])
    return boto3.client('s3', **options)


def _session(token, user_id):
    try:
        data = signing.loads(token, salt=SESSION_SALT, max_age=86400)
        asset = MediaAsset.objects.get(pk=data['asset_id'], owner_id=user_id)
    except (signing.BadSignature, TypeError, KeyError, ValueError, MediaAsset.DoesNotExist):
        raise MediaStorageError('Upload session is invalid, expired or belongs to another user.')
    if data.get('user_id') != user_id or asset.upload_id != data.get('upload_id') or asset.state == 'canceled':
        raise MediaStorageError('Upload session is no longer available.')
    return asset


def start_video_upload(filename, content_type, size_bytes, user_id, purpose='protected'):
    config = settings.MEDIA_STORAGE
    try:
        size = int(size_bytes)
    except (TypeError, ValueError, OverflowError):
        raise MediaStorageError('size_bytes must be a positive whole number.')
    if isinstance(size_bytes, (float, bool)) or size < 1 or size > int(config.get('MAX_MULTIPART_BYTES', 100 * 1024**3)):
        raise MediaStorageError('Video exceeds the configured multipart size limit.')
    name = safe_unique_filename(filename)
    rule = MEDIA_RULES['video']
    if '.' + name.rsplit('.', 1)[-1] not in rule['extensions'] or not isinstance(content_type, str) or content_type not in rule['content_types']:
        raise MediaStorageError('Unsupported video file type.')
    if not isinstance(purpose, str) or purpose not in ('protected', 'public'):
        raise MediaStorageError('Invalid video purpose.')
    bucket, region = config.get('S3_BUCKET_NAME'), config.get('AWS_REGION')
    cdn = config.get('CLOUDFRONT_BASE_URL') or config.get('CDN_DOMAIN')
    if not bucket or not region or not cdn:
        raise MediaStorageError('AWS media storage is not fully configured.')
    part_size = max(5 * 1024**2, min(5 * 1024**3, int(config.get('MULTIPART_PART_SIZE', PART_SIZE))))
    part_size = max(part_size, math.ceil(size / 10000))
    key = f'cbmvideo/{"private/" if purpose == "protected" else ""}{name}'
    client = _client(config)
    try:
        upload_id = client.create_multipart_upload(Bucket=bucket, Key=key, ContentType=content_type)['UploadId']
    except Exception as exc:
        raise MediaStorageError('Could not start multipart upload.') from exc
    try:
        asset = MediaAsset.objects.create(owner_id=user_id, source_key=key,
            reference_url=build_delivery_url(cdn, key), size_bytes=size, part_size=part_size,
            content_type=content_type, purpose=purpose, upload_id=upload_id)
    except Exception:
        client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        raise
    token = signing.dumps({'asset_id': str(asset.pk), 'user_id': user_id, 'upload_id': upload_id}, salt=SESSION_SALT)
    return dict(token=token, part_size=part_size, part_count=math.ceil(size / part_size),
        parallelism=max(1, min(8, int(config.get('MULTIPART_PARALLELISM', 4)))),
        delivery_url=asset.reference_url, media_id=str(asset.pk))


def _params(asset):
    return dict(Bucket=settings.MEDIA_STORAGE['S3_BUCKET_NAME'], Key=asset.source_key, UploadId=asset.upload_id)


def _parts(asset, client):
    parts, marker = [], 0
    while True:
        page = client.list_parts(**_params(asset), PartNumberMarker=marker)
        parts.extend(page.get('Parts', []))
        if not page.get('IsTruncated'):
            return parts
        marker = page['NextPartNumberMarker']


def upload_status(token, user_id):
    asset = _session(token, user_id)
    if asset.state != 'uploading':
        return {'completed': True, 'delivery_url': asset.reference_url, 'parts': []}
    try:
        parts = _parts(asset, _client(settings.MEDIA_STORAGE))
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') == 'NoSuchUpload':
            # S3 may have accepted completion while our response/transaction
            # was lost. HEAD verification in finish is authoritative.
            url = finish_video_upload(token, user_id)
            return {'completed': True, 'delivery_url': url, 'parts': []}
        raise MediaStorageUnavailable('Could not inspect multipart upload. Retry later.') from exc
    except Exception as exc:
        raise MediaStorageUnavailable('Could not inspect multipart upload. Retry later.') from exc
    return {'parts': [{k: p[k] for k in ('PartNumber', 'ETag', 'Size')} for p in parts],
            'part_size': asset.part_size, 'part_count': math.ceil(asset.size_bytes / asset.part_size)}


def sign_part(token, user_id, part_number):
    asset = _session(token, user_id)
    try:
        number = int(part_number)
    except (TypeError, ValueError):
        raise MediaStorageError('Invalid part number.')
    if asset.state != 'uploading' or not 1 <= number <= math.ceil(asset.size_bytes / asset.part_size):
        raise MediaStorageError('Invalid part number or upload state.')
    try:
        return _client(settings.MEDIA_STORAGE).generate_presigned_url('upload_part',
            Params={**_params(asset), 'PartNumber': number}, ExpiresIn=900, HttpMethod='PUT')
    except Exception as exc:
        raise MediaStorageError('Could not sign upload part.') from exc


@transaction.atomic
def finish_video_upload(token, user_id):
    asset = _session(token, user_id)
    asset = MediaAsset.objects.select_for_update().get(pk=asset.pk)
    if asset.state != 'uploading':
        return asset.reference_url
    client = _client(settings.MEDIA_STORAGE)
    try:
        try:
            parts = _parts(asset, client)
            count = math.ceil(asset.size_bytes / asset.part_size)
            if [p['PartNumber'] for p in parts] != list(range(1, count + 1)) or any(
                p.get('Size') != min(asset.part_size, asset.size_bytes - (p['PartNumber'] - 1) * asset.part_size) for p in parts
            ):
                raise MediaStorageError('Upload is incomplete or part sizes differ. Retry the missing parts.')
            client.complete_multipart_upload(**_params(asset), MultipartUpload={
                'Parts': [{k: p[k] for k in ('PartNumber', 'ETag')} for p in parts]})
        except ClientError as exc:
            if exc.response.get('Error', {}).get('Code') != 'NoSuchUpload':
                raise
            # Recover completion whose response/transaction was interrupted.
        head = client.head_object(Bucket=settings.MEDIA_STORAGE['S3_BUCKET_NAME'], Key=asset.source_key)
        if head['ContentLength'] != asset.size_bytes or head['ContentType'] != asset.content_type:
            raise MediaStorageError('Uploaded object does not match its declared size and type.')
    except MediaStorageError:
        raise
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') in ('NoSuchKey', '404', 'NotFound'):
            raise MediaStorageError('This upload no longer exists. Start a new upload.') from exc
        raise MediaStorageUnavailable('Could not complete multipart upload. Retry later.') from exc
    except Exception as exc:
        raise MediaStorageUnavailable('Could not complete multipart upload. Retry later.') from exc
    asset.state = 'uploaded'
    asset.save(update_fields=['state', 'updated_at'])
    return asset.reference_url


@transaction.atomic
def abort_video_upload(token, user_id):
    asset = _session(token, user_id)
    asset = MediaAsset.objects.select_for_update().get(pk=asset.pk)
    if asset.state != 'uploading':
        return  # Never delete a completed/attached source on a late cancel.
    try:
        _client(settings.MEDIA_STORAGE).abort_multipart_upload(**_params(asset))
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') != 'NoSuchUpload':
            raise MediaStorageError('Could not cancel multipart upload.') from exc
    except Exception as exc:
        raise MediaStorageError('Could not cancel multipart upload.') from exc
    asset.state = 'canceled'
    asset.save(update_fields=['state', 'updated_at'])
