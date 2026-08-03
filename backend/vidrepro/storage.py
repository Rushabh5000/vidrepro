"""S3/MinIO object storage helper. All keys are org-prefixed by callers."""
import json
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

from vidrepro.config import get_settings


def _client(endpoint: str):
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        region_name="us-east-1",
    )


@lru_cache
def internal_client():
    return _client(get_settings().s3_endpoint)


@lru_cache
def public_client():
    """Client whose presigned URLs are reachable from the user's browser."""
    return _client(get_settings().public_s3_endpoint)


def bucket() -> str:
    return get_settings().s3_bucket


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    internal_client().put_object(Bucket=bucket(), Key=key, Body=data, ContentType=content_type)


def put_file(key: str, path: str, content_type: str = "application/octet-stream") -> None:
    internal_client().upload_file(path, bucket(), key, ExtraArgs={"ContentType": content_type})


def get_bytes(key: str) -> bytes:
    return internal_client().get_object(Bucket=bucket(), Key=key)["Body"].read()


def download_file(key: str, path: str) -> None:
    internal_client().download_file(bucket(), key, path)


def object_exists(key: str) -> bool:
    try:
        internal_client().head_object(Bucket=bucket(), Key=key)
        return True
    except Exception:
        return False


def object_size(key: str) -> int:
    return internal_client().head_object(Bucket=bucket(), Key=key)["ContentLength"]


def delete_prefix(prefix: str) -> int:
    """Hard-delete every object under a prefix. Returns count."""
    client = internal_client()
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket(), Prefix=prefix):
        objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if objs:
            client.delete_objects(Bucket=bucket(), Delete={"Objects": objs})
            deleted += len(objs)
    return deleted


def presigned_put(key: str, content_type: str, expires: int = 3600) -> str:
    return public_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket(), "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
    )


def presigned_get(key: str, expires: int = 900) -> str:
    return public_client().generate_presigned_url(
        "get_object", Params={"Bucket": bucket(), "Key": key}, ExpiresIn=expires
    )


def put_json(key: str, obj: Any) -> None:
    put_bytes(key, json.dumps(obj, default=str).encode(), "application/json")


def get_json(key: str) -> Any:
    return json.loads(get_bytes(key))
