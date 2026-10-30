"""Shared MinIO client for the RustFS object storage.

This module only builds the client instance; every operation on objects
lives in :mod:`app.storage.service`.
"""

from minio import Minio

from app.core.config import settings

storage_client = Minio(
    endpoint=settings.storage_endpoint,
    access_key=settings.rustfs_access_key,
    secret_key=settings.rustfs_secret_key,
    secure=settings.rustfs_secure,
)