"""Isolation hygiene for DingTalk/OAuth source tests.

``TestDingTalkDirectorySyncErrorMigration`` is a TransactionTestCase that
rewinds migrations and then flushes every table. That deletes the default
Tenant row. After a sibling Django TestCase in the same process, post_migrate
does not reliably recreate it, so later ``create_test_user()`` dies in
Event → dramatiq → ``get_current_tenant()`` and closes the DB connection.

Re-seed the tenant before every DB test so a poisoned leftover cannot leak
into the next class, regardless of pytest-randomly order.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test import TransactionTestCase


def _ensure_connection() -> None:
    closed = connection.connection is None or getattr(connection.connection, "closed", False)
    if closed:
        connection.connect()
    if getattr(connection, "needs_rollback", False):
        connection.rollback()


@pytest.fixture(autouse=True)
def restore_default_tenant(request: pytest.FixtureRequest):
    cls = getattr(request.node, "cls", None)
    # Django TestCase subclasses TransactionTestCase. SimpleTestCase does not.
    if not (isinstance(cls, type) and issubclass(cls, TransactionTestCase)):
        yield
        return

    from authentik.tenants.apps import ensure_default_tenant

    def _restore() -> None:
        _ensure_connection()
        ensure_default_tenant()

    _restore()
    yield
    # Skip post-test restore for the migration class itself: its _post_teardown
    # remigrates and flushes, then restores. Running here would race that.
    if cls.__name__ != "TestDingTalkDirectorySyncErrorMigration":
        _restore()
