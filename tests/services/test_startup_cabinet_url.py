"""Startup/admin links must point at Gorec operational repos."""

from __future__ import annotations

from app.services import startup_notification_service as sns


def test_startup_github_urls_are_gorec():
    assert 'gorecvpn/GorecVPN-' in sns.GITHUB_BOT_URL
    assert 'gorecvpn/Gorec-Cabinet' in sns.GITHUB_CABINET_URL
    assert 'BEDOLAGA' not in sns.GITHUB_CABINET_URL
    assert 'bedolaga' not in sns.GITHUB_CABINET_URL.lower()
