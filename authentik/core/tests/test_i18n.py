"""Test backend internationalization catalog loading."""

from django.utils.translation import gettext, override


def test_backend_simplified_chinese_catalog_loads():
    """Django zh-Hans activation loads the existing simplified Chinese catalog."""
    with override("zh-Hans"):
        assert gettext("Permission denied") == "权限被拒绝"


def test_backend_traditional_chinese_catalog_loads():
    """Django zh-Hant activation loads the existing traditional Chinese catalog."""
    with override("zh-Hant"):
        assert gettext("Permission denied") == "權限不足。"
