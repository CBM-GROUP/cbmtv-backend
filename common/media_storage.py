import re
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import boto3
from botocore.config import Config
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


def encode_object_key(object_key):
    return "/".join(quote(part, safe="-._~") for part in object_key.strip("/").split("/"))


def build_delivery_url(base_url, object_key):
    if not base_url:
        raise MediaStorageError("CLOUDFRONT_BASE_URL is not configured.")
    return f"{base_url.rstrip('/')}/{encode_object_key(object_key)}"


def build_s3_public_url(bucket, region, object_key, base_url=""):
    """Public S3 URL for an object, used for images.

    Images are served straight from the bucket, NOT through CloudFront, by
    decision rather than necessity. Videos keep using CloudFront.
    """
    if base_url:
        return f"{base_url.rstrip('/')}/{encode_object_key(object_key)}"
    if not bucket:
        raise MediaStorageError("AWS_STORAGE_BUCKET_NAME is not configured.")
    host = f"{bucket}.s3.{region}.amazonaws.com" if region else f"{bucket}.s3.amazonaws.com"
    return f"https://{host}/{encode_object_key(object_key)}"


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
    image_base_url = config.get("IMAGE_BASE_URL", "")
    if not bucket or not region:
        raise MediaStorageError("AWS media storage is not fully configured.")
    # Only video delivery goes through CloudFront, so only video needs the CDN.
    if media_type == "video" and not cdn_url:
        raise MediaStorageError("AWS media storage is not fully configured.")

    generated_filename = safe_unique_filename(filename)
    object_key = f'{rule["prefix"]}/{generated_filename}'
    # Delivery differs by media type:
    #
    #   video -> CloudFront, using the FULL object key including the cbmvideo/
    #            prefix. The distribution's origin has no origin path, so the
    #            request URI is the S3 key verbatim. Verified live:
    #              <cdn>/cbmvideo/<file>.mp4 -> 200
    #              <cdn>/<file>.mp4          -> 403
    #            NOTE: this distribution previously had an origin path of
    #            /cbmvideo, which required the opposite (stripping the prefix).
    #            That origin path was removed, inverting the correct shape. If
    #            video URLs 404/403, re-check OriginPath on distribution
    #            E2QQRMV897UN2I before changing this.
    #
    #   image -> straight from S3 at the cbm-images/ key, deliberately bypassing
    #            CloudFront. (The CDN would serve them now that the origin path
    #            is gone, but images are intentionally not routed through it.)
    if media_type == "video":
        delivery_url = build_delivery_url(cdn_url, object_key)
    else:
        delivery_url = build_s3_public_url(bucket, region, object_key, image_base_url)
    # Pin SigV4. Against the global s3.amazonaws.com endpoint boto3 otherwise
    # falls back to SigV2, which AWS has deprecated and which only works in
    # regions launched before 2014.
    client_options = {
        "region_name": region,
        "config": Config(signature_version="s3v4"),
    }
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
        "delivery_url": delivery_url,
        "headers": {"Content-Type": content_type},
    }
