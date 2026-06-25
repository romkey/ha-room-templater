"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from tests.ha_mock import HAMock, build_jinja_env, kitchen_fixture


@pytest.fixture
def kitchen_ha() -> HAMock:
    return kitchen_fixture()


@pytest.fixture
def kitchen_env(kitchen_ha: HAMock):
    return build_jinja_env(kitchen_ha)
