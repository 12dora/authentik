"""pytest hooks for OAuth source tests (DingTalk 等).

Authentik 有若干 lifecycle system migration 建的 unmanaged 表
(authentik_install_id / authentik_version_history)。pytest-django 建测试库
时只跑 Django migrations, 这里补齐这些表以免测试 setup 失败。
"""

from __future__ import annotations

import pytest
from django.db import connection


def _ensure_unmanaged_tables() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS authentik_install_id (
                id TEXT NOT NULL
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS authentik_version_history (
                id BIGSERIAL PRIMARY KEY,
                "timestamp" timestamp with time zone NOT NULL,
                version text NOT NULL,
                build text NOT NULL
            );
            """
        )
        cursor.execute("SELECT COUNT(*) FROM authentik_install_id;")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO authentik_install_id (id) VALUES ('test-install-id');")


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):  # noqa: ARG001
    with django_db_blocker.unblock():
        _ensure_unmanaged_tables()
