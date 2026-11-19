"""Unit tests for utils.py's form-value parsers and single-use form
tokens — previously untested on their own (only exercised indirectly
through whichever route happened to call them). Covers the edge cases
that actually matter: the boundary between accepted/rejected, and the
NaN/Infinity gap a naive float->Decimal port would otherwise reopen
(see parse_non_negative_decimal()'s own docstring in utils.py).
"""
import decimal

import pytest
from flask import request, session

from heaven_angel.utils import (ValidationError, consume_form_token, issue_form_token,
                    parse_non_negative_decimal, parse_non_negative_int,
                    parse_past_date, parse_positive_decimal,
                    parse_positive_int, parse_required_text, percent_change,
                    product_avatar)


# ---------------------------------------------------------------- parse_positive_int
def test_parse_positive_int_accepts_a_positive_value():
    assert parse_positive_int("5") == 5


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "", None, "1.5"])
def test_parse_positive_int_rejects_non_positive_or_invalid(raw):
    with pytest.raises(ValidationError):
        parse_positive_int(raw)


# ---------------------------------------------------------------- parse_non_negative_int
def test_parse_non_negative_int_accepts_zero():
    assert parse_non_negative_int("0") == 0


def test_parse_non_negative_int_rejects_negative():
    with pytest.raises(ValidationError):
        parse_non_negative_int("-1")


# ---------------------------------------------------------------- parse_non_negative_decimal
def test_parse_non_negative_decimal_returns_a_decimal_not_a_float():
    value = parse_non_negative_decimal("19.99")
    assert isinstance(value, decimal.Decimal)
    assert value == decimal.Decimal("19.99")


def test_parse_non_negative_decimal_accepts_zero():
    assert parse_non_negative_decimal("0") == 0


def test_parse_non_negative_decimal_rejects_negative():
    with pytest.raises(ValidationError):
        parse_non_negative_decimal("-0.01")


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "infinity"])
def test_parse_non_negative_decimal_rejects_nan_and_infinity(raw):
    """The whole reason this isn't just `if value < 0: raise` — NaN/
    Infinity are never < 0, so that check alone would accept them as
    "valid", and comparing a Decimal NaN with < raises InvalidOperation
    if not guarded, which would surface as a raw 500 instead of a
    friendly ValidationError."""
    with pytest.raises(ValidationError):
        parse_non_negative_decimal(raw)


# ---------------------------------------------------------------- parse_positive_decimal
def test_parse_positive_decimal_rejects_zero():
    with pytest.raises(ValidationError):
        parse_positive_decimal("0")


def test_parse_positive_decimal_rejects_negative():
    with pytest.raises(ValidationError):
        parse_positive_decimal("-5")


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_parse_positive_decimal_rejects_nan_and_infinity(raw):
    with pytest.raises(ValidationError):
        parse_positive_decimal(raw)


def test_parse_positive_decimal_accepts_a_positive_value():
    assert parse_positive_decimal("0.01") == decimal.Decimal("0.01")


# ---------------------------------------------------------------- parse_past_date
def test_parse_past_date_rejects_a_future_date():
    import datetime
    tomorrow = (datetime.date.today() +
                datetime.timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError):
        parse_past_date(tomorrow)


def test_parse_past_date_rejects_an_invalid_date_string():
    with pytest.raises(ValidationError):
        parse_past_date("not-a-date")


# ---------------------------------------------------------------- product_avatar
def test_product_avatar_is_deterministic_for_the_same_name():
    """Same product, looked up on two different pages (catalog vs.
    inventory) — must get the exact same initials both times, not a
    freshly re-derived one per render."""
    first = product_avatar("Black Opium")
    second = product_avatar("Black Opium")
    assert first == second


def test_product_avatar_initials_stable_regardless_of_casing_or_spacing():
    """The same product name typed/stored with different whitespace or
    casing (e.g. trailing space from a form) should still resolve to the
    same initials."""
    assert product_avatar("Black Opium")["initials"] == product_avatar(
        "  black   opium  ")["initials"]


def test_product_avatar_uses_initials_from_two_words():
    assert product_avatar("Black Opium")["initials"] == "BO"


def test_product_avatar_uses_first_two_letters_of_a_single_word():
    assert product_avatar("Vanille")["initials"] == "VA"


def test_product_avatar_handles_blank_name():
    av = product_avatar("")
    assert av["initials"] == "?"


# ---------------------------------------------------------------- single-use form tokens
#
# Flask's session is only actually persisted (signed into a cookie) when
# a real response goes through the app's dispatch cycle — a bare
# `test_request_context()` doesn't do that, so session writes made in
# one `with` block are simply gone in a separate one. These tests do
# every issue+consume within a single request context instead (setting
# request.form directly, since the token isn't known until
# issue_form_token() returns it) rather than trying to round-trip a
# session across two contexts the way two real requests would.
def test_consume_form_token_succeeds_once_then_fails_on_reuse(app):
    with app.test_request_context():
        token = issue_form_token("test_form")
        request.form = {"form_token": token}
        assert consume_form_token("test_form") is True
        # The session slot was popped by the call above — a second
        # attempt with the identical token must fail, same as a real
        # double-submit would.
        assert consume_form_token("test_form") is False


def test_consume_form_token_fails_with_no_token_issued(app):
    with app.test_request_context():
        request.form = {"form_token": "whatever-a-client-sent"}
        assert consume_form_token("never_issued") is False


def test_consume_form_token_fails_with_the_wrong_value(app):
    with app.test_request_context():
        issue_form_token("test_form_2")
        request.form = {"form_token": "not-the-real-token"}
        assert consume_form_token("test_form_2") is False


# ---------------------------------------------------------------- percent_change
def test_percent_change_up_and_down():
    assert percent_change(decimal.Decimal("150"), decimal.Decimal("100")) == 50.0
    assert percent_change(75, 100) == -25.0
    assert percent_change(100, 100) == 0.0


def test_percent_change_rounds_to_one_decimal():
    assert percent_change(2, 3) == -33.3


def test_percent_change_is_none_without_a_baseline():
    # Nothing last period -> no meaningful percentage.
    assert percent_change(500, 0) is None
    assert percent_change(0, decimal.Decimal("0")) is None


# ---------------------------------------------------------------- hardening
def test_issue_form_token_is_stable_until_it_is_used(app):
    # A live-refresh GET must not rotate the token under a half-filled form.
    with app.test_request_context():
        first = issue_form_token("stable_form")
        assert issue_form_token("stable_form") == first
        request.form = {"form_token": first}
        assert consume_form_token("stable_form") is True
        assert issue_form_token("stable_form") != first


def test_a_replayed_cookie_cannot_use_a_form_token_twice(app):
    # The session is a client-side cookie: two requests carrying the SAME cookie
    # (double-click, two tabs) must not both be accepted.
    with app.test_request_context():
        token = issue_form_token("replay_form")
        saved = dict(session)
    for _ in range(2):
        with app.test_request_context():
            session.update(saved)
            request.form = {"form_token": token}
            results = consume_form_token("replay_form")
            if _ == 0:
                assert results is True
            else:
                assert results is False


def test_required_text_rejects_line_breaks():
    with pytest.raises(ValidationError):
        parse_required_text("Acme\r\nBcc: x@evil.test", "Company")
    assert parse_required_text("  Acme Corp ", "Company") == "Acme Corp"


def test_excel_text_cells_cannot_become_formulas():
    from heaven_angel.services.reports.excel import _safe_text
    assert _safe_text("=1+1").startswith("'=")
    assert _safe_text("@SUM(A1)").startswith("'@")
    assert _safe_text("Plain text") == "Plain text"
    assert _safe_text(5) == "5"


def test_portal_slug_is_cleaned_of_pasted_links():
    from heaven_angel.config import clean_portal_slug
    assert clean_portal_slug("abc_DEF-123") == "abc_DEF-123"
    assert clean_portal_slug("abc_DEF-123/packages") == "abc_DEF-123"
    assert clean_portal_slug("https://x.test/partner-portal/abc_DEF-123/packages") == "abc_DEF-123"
    assert clean_portal_slug("  /abc/  ") == "abc"
    assert clean_portal_slug(None) == ""
