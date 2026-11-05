"""Small, bounded image ingestion; video uses the existing multipart/HLS queue."""
import io
import uuid
import warnings
from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.exceptions import ValidationError, APIException
from common.multipart_upload import _client
from common.media_storage import build_s3_public_url
from .models import ImageAsset


class StorageUnavailable(APIException):
    status_code = 503
    default_detail = 'Advertising media storage is unavailable.'


def upload_image(file, user):
    if not file or file.size > 5 * 1024 * 1024 or file.size < 1 or file.content_type not in ('image/jpeg', 'image/png', 'image/webp'):
        raise ValidationError('Upload a JPEG, PNG or WebP image up to 5 MiB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            raw = file.read(5 * 1024 * 1024 + 1)
            with Image.open(io.BytesIO(raw)) as probe:
                if Image.MIME.get(probe.format) != file.content_type or getattr(probe, 'n_frames', 1) != 1:
                    raise ValueError()
                width, height = probe.size
                if width < 320 or height < 90 or width > 8192 or height > 8192 or width * height > 20_000_000:
                    raise ValueError()
                probe.verify()
            with Image.open(io.BytesIO(raw)) as decoded:
                image = ImageOps.exif_transpose(decoded).convert('RGB')
                width, height = image.size
                outputs = []
                for maximum in (1600, 640):
                    variant = image.copy()
                    variant.thumbnail((maximum, maximum))
                    buffer = io.BytesIO()
                    variant.save(buffer, format='WEBP', quality=85, method=4)
                    outputs.append(buffer.getvalue())
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationError('Use a valid still image, 320–8192 px wide, 90–8192 px high, up to 20 megapixels.') from None
    config = settings.MEDIA_STORAGE
    asset_id = uuid.uuid4()
    urls = []
    try:
        client = _client(config)
        for label, body in zip(('desktop', 'mobile'), outputs):
            key = f'cbm-images/advertising/{asset_id}/{label}.webp'
            client.put_object(Bucket=config['S3_BUCKET_NAME'], Key=key, Body=body, ContentType='image/webp',
                              CacheControl='public,max-age=31536000,immutable')
            urls.append(build_s3_public_url(config['S3_BUCKET_NAME'], config.get('AWS_REGION', ''), key, config.get('IMAGE_BASE_URL', '')))
    except Exception:
        raise StorageUnavailable() from None
    return ImageAsset.objects.create(id=asset_id, owner=user, desktop_url=urls[0], mobile_url=urls[1],
                                     width=width, height=height, size_bytes=sum(map(len, outputs)))
