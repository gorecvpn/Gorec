"""Raffle prize image upload helpers and size/type contract."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.cabinet.routes.admin_raffle import (
    _MAX_RAFFLE_IMAGE_BYTES,
    _build_upload_url,
    _upload_response,
)
from app.services.news_media_service import SavedMedia


def test_raffle_image_max_size_is_5mb():
    assert _MAX_RAFFLE_IMAGE_BYTES == 5 * 1024 * 1024


def test_build_upload_url_returns_site_relative_path():
    """Cabinet Mini App must get /uploads/... (not absolute cabinet-host URL)."""
    request = MagicMock()
    request.url.scheme = 'http'
    request.url.netloc = 'internal:8080'
    request.headers = {
        'X-Forwarded-Proto': 'https',
        'X-Forwarded-Host': 'cabinet.example.com',
    }
    assert _build_upload_url(request, 'images/abc.jpg') == '/uploads/images/abc.jpg'
    assert _build_upload_url(request, '/images/abc.jpg') == '/uploads/images/abc.jpg'


def test_upload_response_includes_thumbnail():
    request = MagicMock()
    request.url.scheme = 'https'
    request.url.netloc = 'example.com'
    request.headers = {}
    saved = SavedMedia(
        filename='abc.jpg',
        relative_path='images/abc.jpg',
        thumbnail_path='thumbnails/thumb_abc.jpg',
        media_type='image',
        content_type='image/jpeg',
        size_bytes=1234,
        width=800,
        height=600,
    )
    resp = _upload_response(request, saved)
    assert resp.url == '/uploads/images/abc.jpg'
    assert resp.thumbnail_url == '/uploads/thumbnails/thumb_abc.jpg'
    assert resp.filename == 'abc.jpg'
    assert resp.media_type == 'image'
    assert resp.size_bytes == 1234
