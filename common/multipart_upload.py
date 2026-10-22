"""Authenticated, direct-to-S3 multipart video uploads.

The signed session binds the S3 upload id and key to the dashboard user. No
AWS credentials or arbitrary S3 object operations are exposed to the browser.
"""

import math

import boto3
from botocore.config import Config
from django.conf import settings
from django.core import signing

from .media_storage import MediaStorageError, build_delivery_url, parse_size_bytes, safe_unique_filename


PART_SIZE = 8 * 1024 * 1024
SESSION_SALT = "cbmtv-multipart-video-v1"


def _client(config):
    options = {"region_name": config["AWS_REGION"], "config": Config(signature_version="s3v4")}
    if config.get("CREDENTIALS_MODE") == "access_keys":
        access_key = config.get("AWS_ACCESS_KEY_ID")
        secret_key = config.get("AWS_SECRET_ACCESS_KEY")
        if not access_key or not secret_key:
            raise MediaStorageError("AWS media storage credentials are not configured.")
        options.update(aws_access_key_id=access_key, aws_secret_access_key=secret_key)
    return boto3.client("s3", **options)


def _session(token, user_id):
    try:
        data = signing.loads(token, salt=SESSION_SALT, max_age=24 * 3600)
    except (signing.BadSignature, signing.SignatureExpired, TypeError):
        raise MediaStorageError("Upload session is invalid or expired.")
    if data.get("user_id") != user_id or not data.get("key") or not data.get("upload_id"):
        raise MediaStorageError("Upload session does not belong to this user.")
    return data


def start_video_upload(filename, content_type, size_bytes, user_id):
    config = settings.MEDIA_STORAGE
    size = parse_size_bytes(size_bytes, config)
    if not size or size < PART_SIZE:
        raise MediaStorageError("Multipart video must be at least 8 MB.")
    extension = filename.rsplit(".", 1)[-1].lower() if isinstance(filename, str) else ""
    if f".{extension}" not in {".mp4", ".m4v", ".mov", ".webm"} or content_type not in {
        "video/mp4", "video/x-m4v", "video/quicktime", "video/webm"
    }:
        raise MediaStorageError("Unsupported video file type.")
    bucket = config.get("S3_BUCKET_NAME")
    region = config.get("AWS_REGION")
    cdn = config.get("CLOUDFRONT_BASE_URL") or config.get("CDN_DOMAIN")
    if not bucket or not region or not cdn:
        raise MediaStorageError("AWS media storage is not fully configured.")
    key = f"cbmvideo/{safe_unique_filename(filename)}"
    try:
        upload_id = _client(config).create_multipart_upload(
            Bucket=bucket, Key=key, ContentType=content_type
        )["UploadId"]
    except Exception as exc:
        raise MediaStorageError("Could not start multipart upload.") from exc
    token = signing.dumps({
        "user_id": user_id, "key": key, "upload_id": upload_id,
        "part_count": math.ceil(size / PART_SIZE),
    }, salt=SESSION_SALT)
    return {
        "token": token,
        "part_size": PART_SIZE,
        "part_count": math.ceil(size / PART_SIZE),
        "delivery_url": build_delivery_url(cdn, key),
    }


def sign_part(token, user_id, part_number):
    session = _session(token, user_id)
    try:
        number = int(part_number)
    except (TypeError, ValueError):
        raise MediaStorageError("Invalid part number.")
    if number < 1 or number > session["part_count"]:
        raise MediaStorageError("Invalid part number.")
    config = settings.MEDIA_STORAGE
    try:
        return _client(config).generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": config["S3_BUCKET_NAME"], "Key": session["key"],
                "UploadId": session["upload_id"], "PartNumber": number,
            },
            ExpiresIn=min(3600, int(config.get("PRESIGNED_URL_TTL", 3600))),
            HttpMethod="PUT",
        )
    except Exception as exc:
        raise MediaStorageError("Could not sign upload part.") from exc


def finish_video_upload(token, user_id):
    session = _session(token, user_id)
    config = settings.MEDIA_STORAGE
    client = _client(config)
    params = {"Bucket": config["S3_BUCKET_NAME"], "Key": session["key"], "UploadId": session["upload_id"]}
    parts = []
    marker = 0
    try:
        while True:
            page = client.list_parts(**params, PartNumberMarker=marker)
            parts.extend({"PartNumber": part["PartNumber"], "ETag": part["ETag"]} for part in page.get("Parts", []))
            if not page.get("IsTruncated"):
                break
            marker = page["NextPartNumberMarker"]
        if [part["PartNumber"] for part in parts] != list(range(1, session["part_count"] + 1)):
            raise MediaStorageError("Upload is incomplete. Retry the missing parts.")
        client.complete_multipart_upload(**params, MultipartUpload={"Parts": parts})
    except MediaStorageError:
        raise
    except Exception as exc:
        raise MediaStorageError("Could not complete multipart upload.") from exc
    cdn = config.get("CLOUDFRONT_BASE_URL") or config.get("CDN_DOMAIN")
    return build_delivery_url(cdn, session["key"])


def abort_video_upload(token, user_id):
    session = _session(token, user_id)
    config = settings.MEDIA_STORAGE
    try:
        _client(config).abort_multipart_upload(
            Bucket=config["S3_BUCKET_NAME"], Key=session["key"], UploadId=session["upload_id"]
        )
    except Exception as exc:
        raise MediaStorageError("Could not cancel multipart upload.") from exc
