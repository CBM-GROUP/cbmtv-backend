import re
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import boto3
from django.conf import settings


class MediaStorageError(Exception):
    pass


MEDIA_RULES = {
    "video": {
        "prefix": "cbmvideo",
        "extensions": {".mp4", ".m4v", ".mov", ".webm"},
        "content_types": {"video/mp4", "video/x-m4v", "video/quicktime", "video/webm"},
    },
    "image": {
        "prefix": "cbm-images",
        "extensions": {".jpg", ".jpeg", ".png", ".webp", ".gif"},
        "content_types": {"image/jpeg", "image/png", "image/webp", "image/gif"},
    },
}


def safe_unique_filename(filename):
    if not isinstance(filename, str) or not filename.strip():
        raise MediaStorageError("A filename is required.")
    basename = Path(filename.replace("\\", "/")).name
    stem = Path(basename).stem.strip()
    extension = Path(basename).suffix.lower()
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("._-") or "media"
    return f"{uuid4().hex}-{safe_stem}{extension}"


def build_delivery_url(base_url, object_key):
    if not base_url:
        raise MediaStorageError("CLOUDFRONT_BASE_URL is not configured.")
    encoded_key = "/".join(quote(part, safe="-._~") for part in object_key.strip("/").split("/"))
    return f"{base_url.rstrip('/')}/{encoded_key}"


def create_upload_target(filename, content_type, media_type):
    rule = MEDIA_RULES.get(media_type)
    if not rule:
        raise MediaStorageError("Unsupported media type.")

    extension = Path(Path(str(filename).replace("\\", "/")).name).suffix.lower()
    if extension not in rule["extensions"] or content_type not in rule["content_types"]:
        raise MediaStorageError(f"Unsupported {media_type} file type.")

    config = settings.MEDIA_STORAGE
    bucket = config.get("S3_BUCKET_NAME")
    region = config.get("AWS_REGION")
    cdn_url = config.get("CLOUDFRONT_BASE_URL") or config.get("CDN_DOMAIN")
    if not bucket or not region or not cdn_url:
        raise MediaStorageError("AWS media storage is not fully configured.")

    object_key = f'{rule["prefix"]}/{safe_unique_filename(filename)}'
    client_options = {"region_name": region}
    if config.get("CREDENTIALS_MODE") == "access_keys":
        access_key = config.get("AWS_ACCESS_KEY_ID")
        secret_key = config.get("AWS_SECRET_ACCESS_KEY")
        if not access_key or not secret_key:
            raise MediaStorageError("AWS media storage credentials are not configured.")
        client_options.update(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    try:
        s3 = boto3.client("s3", **client_options)
        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": object_key, "ContentType": content_type},
            ExpiresIn=config.get("PRESIGNED_URL_TTL", 3600),
            HttpMethod="PUT",
        )
    except Exception as exc:
        raise MediaStorageError("Could not create the upload target.") from exc

    return {
        "upload_url": upload_url,
        "object_key": object_key,
        "delivery_url": build_delivery_url(cdn_url, object_key),
        "headers": {"Content-Type": content_type},
    }
