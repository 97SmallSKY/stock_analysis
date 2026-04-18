"""tests/test_logging_redact.py — W11-3 脱敏单测"""
from __future__ import annotations

import pytest

from backend.core.logging import redact, setup_logging


@pytest.mark.parametrize("inp, expect_masked_key, must_not_contain", [
    ("GET /x?token=abc123&page=2",                "token",         "abc123"),
    ("header: authorization: Bearer abc.def.ghi",  "authorization", "abc.def.ghi"),
    ("header: Authorization: Basic ZGVtbzpkZW1v",  "Authorization", "ZGVtbzpkZW1v"),
    ('json payload {"api_key":"XYZ","q":1}',       "api_key",       "XYZ"),
    ("Cookie: abc=def",                            "Cookie",        "def"),
    ("password=hunter2",                           "password",      "hunter2"),
    ("Set-Cookie: sid=12345",                      "Set-Cookie",    "12345"),
    ("x-api-key: my-secret-key",                   "x-api-key",     "my-secret-key"),
])
def test_redact_common_shapes(inp, expect_masked_key, must_not_contain):
    out = redact(inp)
    # 原 key 名应保留以便日志可读
    assert expect_masked_key.lower() in out.lower()
    if must_not_contain is not None:
        assert must_not_contain not in out
        assert "***" in out


def test_redact_leaves_non_sensitive():
    out = redact("page=2&limit=50&ts_code=600000.SH")
    assert "page=2" in out and "limit=50" in out and "ts_code=600000.SH" in out
    assert "***" not in out


def test_redact_empty_and_non_str():
    assert redact("") == ""
    assert redact(None) is None  # type: ignore[arg-type]
    assert redact(123) == 123    # type: ignore[arg-type]


def test_setup_logging_idempotent_no_crash():
    # 至少保证不抛
    setup_logging("DEBUG")
    setup_logging("INFO")


def test_setup_logging_bridges_stdlib_to_loguru(capsys):
    import logging
    setup_logging("INFO")
    logging.getLogger("redact_probe").warning(
        "request with token=SHOULD_NOT_APPEAR and page=2"
    )
    err = capsys.readouterr().err
    assert "SHOULD_NOT_APPEAR" not in err
    assert "***" in err
    assert "page=2" in err
