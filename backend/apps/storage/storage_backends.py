from __future__ import annotations

from storages.backends.s3boto3 import S3Boto3Storage
from django.conf import settings


class MediaStorage(S3Boto3Storage):
    bucket_name = settings.S3_BUCKET_MEDIA
    default_acl = None
    file_overwrite = False
    querystring_auth = True
    custom_domain = None

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("endpoint_url", settings.S3_ENDPOINT_URL)
        kwargs.setdefault("access_key", settings.S3_ACCESS_KEY_ID)
        kwargs.setdefault("secret_key", settings.S3_SECRET_ACCESS_KEY)
        kwargs.setdefault("region_name", settings.S3_REGION_NAME)
        super().__init__(*args, **kwargs)
