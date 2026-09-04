from __future__ import annotations

from pathlib import Path
import os

import environ
from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_SECRET_KEY=(str, "change-me"),
    DJANGO_ALLOWED_HOSTS=(str, "localhost,127.0.0.1"),
)

if (REPO_ROOT / ".env").exists():
    environ.Env.read_env(REPO_ROOT / ".env")

DEBUG = env.bool("DJANGO_DEBUG")
SECRET_KEY = env.str("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = [h.strip() for h in env.str("DJANGO_ALLOWED_HOSTS").split(",") if h.strip()]

CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in env.str("DJANGO_CSRF_TRUSTED_ORIGINS", default="").split(",") if o.strip()
]

INSTALLED_APPS = [
    "unfold.apps.DefaultAppConfig",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.documents.apps.DocumentsConfig",
    "apps.dataset.apps.DatasetConfig",
    "apps.accounts.apps.AccountsConfig",
    "apps.storage.apps.StorageConfig",
    "apps.layout.apps.LayoutConfig",
    "apps.llm.apps.LLMConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "ocr_platform.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "ocr_platform.wsgi.application"
ASGI_APPLICATION = "ocr_platform.asgi.application"

DATABASES = {"default": env.db("DATABASE_URL", default="postgres://ocr_platform:ocr_platform@localhost:5432/ocr_platform")}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "vi"
TIME_ZONE = "Asia/Ho_Chi_Minh"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = REPO_ROOT / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = REPO_ROOT / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_NUMBER_FILES = 500

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_REFERRER_POLICY = "same-origin"
    # Reverse proxy (nginx/traefik) terminate HTTPS phía trước container
    if env.bool("DJANGO_BEHIND_PROXY", default=False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
        USE_X_FORWARDED_HOST = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# MinIO / S3 (tùy chọn)
S3_ENDPOINT_URL = env.str("S3_ENDPOINT_URL", default="")
S3_ACCESS_KEY_ID = env.str("S3_ACCESS_KEY_ID", default="")
S3_SECRET_ACCESS_KEY = env.str("S3_SECRET_ACCESS_KEY", default="")
S3_REGION_NAME = env.str("S3_REGION_NAME", default="us-east-1")
S3_BUCKET_MEDIA = env.str("S3_BUCKET_MEDIA", default="ocr-media")

USE_S3 = bool(S3_ENDPOINT_URL and S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY)
if USE_S3:
    AWS_S3_ENDPOINT_URL = S3_ENDPOINT_URL
    AWS_ACCESS_KEY_ID = S3_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY = S3_SECRET_ACCESS_KEY
    AWS_S3_REGION_NAME = S3_REGION_NAME
    AWS_DEFAULT_ACL = None
    AWS_S3_ADDRESSING_STYLE = "path"
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    STORAGES["default"] = {"BACKEND": "apps.storage.storage_backends.MediaStorage"}

# Celery
CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", default=env.str("REDIS_URL", default="redis://localhost:6379/0"))
CELERY_RESULT_BACKEND = env.str("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 86400}
CELERY_TASK_DEFAULT_QUEUE = "cpu"
CELERY_TASK_ROUTES = {
    "apps.documents.tasks.process_book": {"queue": "gpu"},
    "apps.documents.tasks.clean_book_text": {"queue": "cpu"},
    "apps.documents.tasks.clean_single_page": {"queue": "cpu"},
    "apps.documents.tasks.classify_book": {"queue": "cpu"},
    "apps.dataset.tasks.chunk_source_document": {"queue": "cpu"},
    "apps.dataset.tasks.split_source_chunks": {"queue": "cpu"},
    "apps.dataset.tasks.classify_source_document": {"queue": "cpu"},
    "apps.dataset.tasks.sync_source_to_ragflow": {"queue": "cpu"},
    "apps.dataset.tasks.update_ragflow_chunk": {"queue": "cpu"},
}

if env.bool("CELERY_FORCE_CPU", default=False):
    CELERY_TASK_ROUTES = {"apps.documents.tasks.*": {"queue": "cpu"}}

UNFOLD = {
    "SITE_TITLE": "OCR Sách",
    "SITE_HEADER": "OCR Sách",
    "SITE_URL": "/admin/",
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Tài liệu",
                "items": [
                    {
                        "title": "Sách",
                        "icon": "menu_book",
                        "link": reverse_lazy("admin:documents_book_changelist"),
                    },
                    {
                        "title": "Sách chờ nhập",
                        "icon": "upload_file",
                        "link": reverse_lazy("admin:documents_stagedbook_changelist"),
                    },
                    {
                        "title": "Sách trùng tên",
                        "icon": "content_copy",
                        "link": reverse_lazy("admin:documents_book_duplicate_titles"),
                    },
                    {
                        "title": "Thống kê sách",
                        "icon": "bar_chart",
                        "link": reverse_lazy("admin:documents_book_classification_stats"),
                    },
                    {
                        "title": "Nhóm phân loại",
                        "icon": "category",
                        "link": reverse_lazy("admin:documents_classificationgroup_changelist"),
                    },
                ],
            },
            {
                "title": "Dataset",
                "separator": True,
                "items": [
                    {
                        "title": "Văn bản nguồn",
                        "icon": "article",
                        "link": reverse_lazy("admin:dataset_sourcedocument_changelist"),
                    },
                    {
                        "title": "Phân đoạn",
                        "icon": "segment",
                        "link": reverse_lazy("admin:dataset_chunkeddocument_changelist"),
                    },
                    {
                        "title": "RAGFlow",
                        "icon": "cloud_sync",
                        "link": reverse_lazy("admin:dataset_ragflowconfig_changelist"),
                    },
                ],
            },
            {
                "title": "OCR",
                "separator": True,
                "items": [
                    {
                        "title": "OCR theo loại trang",
                        "icon": "tune",
                        "link": reverse_lazy("admin:llm_pageocrconfig_changelist"),
                    },
                    {
                        "title": "Công cụ OCR",
                        "icon": "build",
                        "link": reverse_lazy("admin:llm_ocrprovider_changelist"),
                    },
                ],
            },
            {
                "title": "LLM",
                "separator": True,
                "items": [
                    {
                        "title": "LLM",
                        "icon": "psychology",
                        "link": reverse_lazy("admin:llm_llmprovider_changelist"),
                    },
                    {
                        "title": "Làm sạch văn bản",
                        "icon": "auto_fix_high",
                        "link": reverse_lazy("admin:llm_textcleaningconfig_changelist"),
                    },
                    {
                        "title": "Phân loại sách",
                        "icon": "auto_awesome",
                        "link": reverse_lazy("admin:llm_bookclassifyconfig_changelist"),
                    },
                    {
                        "title": "Phân đoạn",
                        "icon": "splitscreen",
                        "link": reverse_lazy("admin:llm_chunkingconfig_changelist"),
                    },
                    {
                        "title": "Tạo sách từ upload",
                        "icon": "post_add",
                        "link": reverse_lazy("admin:llm_bookingestconfig_changelist"),
                    },
                    {
                        "title": "Kiểm tra sách trùng",
                        "icon": "find_replace",
                        "link": reverse_lazy("admin:llm_bookdedupeconfig_changelist"),
                    },
                ],
            },
            {
                "title": "Bố cục",
                "separator": True,
                "items": [
                    {
                        "title": "Cấu hình doclayout-yolo",
                        "icon": "view_quilt",
                        "link": reverse_lazy("admin:layout_detectorconfig_changelist"),
                    },
                ],
            },
            {
                "title": "Hệ thống",
                "separator": True,
                "items": [
                    {
                        "title": "Người dùng",
                        "icon": "person",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                    },
                    {
                        "title": "Nhóm quyền",
                        "icon": "group",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                    },
                ],
            },
        ],
    },
}

# Ollama (OCR tùy chọn – chạy ngoài docker compose)
OLLAMA_BASE_URL = env.str("OLLAMA_BASE_URL", default="http://172.17.193.2:11434")
OLLAMA_MODEL = env.str("OLLAMA_MODEL", default="qwen2.5vl:32b")

# Google Cloud (credentials dùng chung cho Vision & Document AI)
GOOGLE_APPLICATION_CREDENTIALS = env.str("GOOGLE_APPLICATION_CREDENTIALS", default="")
DOCUMENT_AI_PROJECT_ID = env.str("DOCUMENT_AI_PROJECT_ID", default="global-axe-477808-s9")
DOCUMENT_AI_LOCATION = env.str("DOCUMENT_AI_LOCATION", default="us")
DOCUMENT_AI_OCR_PROCESSOR_ID = env.str("DOCUMENT_AI_OCR_PROCESSOR_ID", default="")
DOCUMENT_AI_LAYOUT_PROCESSOR_ID = env.str("DOCUMENT_AI_LAYOUT_PROCESSOR_ID", default="")

# GPU: chỉ số card vật lý trên host (nvidia-smi). Docker worker map qua NVIDIA_VISIBLE_DEVICES.
GPU_DEVICE_IDS = env.str("GPU_DEVICE_IDS", default="0")
