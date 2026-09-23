"""
The error vocabulary: provider codes must become decisions, not exceptions.
"""

from __future__ import annotations
import pytest

from tools.errors import (
    META_ERROR_CODES,
    RETRYABLE_ERRORS,
    SendErrorCode,
    TWILIO_ERROR_CODES,
    from_http_status,
    hint_for,
    is_retryable,
    suggested_fallbacks,
)


class TestSendErrorCode:
    """The normalized vocabulary itself."""

    def test_every_code_has_a_hint(self) -> None:
        for code in SendErrorCode:
            hint = hint_for(code)
            assert hint and len(hint) > 20, code

    def test_retryable_set_is_a_subset_of_the_vocabulary(self) -> None:
        assert RETRYABLE_ERRORS <= set(SendErrorCode)

    def test_transient_errors_are_retryable_and_consent_errors_are_not(self) -> None:
        assert is_retryable(SendErrorCode.RATE_LIMITED)
        assert is_retryable(SendErrorCode.NETWORK_ERROR)
        assert is_retryable(SendErrorCode.PROVIDER_UNAVAILABLE)
        assert not is_retryable(SendErrorCode.RECIPIENT_NOT_VERIFIED)
        assert not is_retryable(SendErrorCode.OPTED_OUT)

    def test_unknown_codes_are_not_retryable(self) -> None:
        assert not is_retryable(SendErrorCode.UNKNOWN)


class TestSuggestedFallbacks:
    """Which channels are worth trying after a given failure."""

    def test_not_verified_suggests_sms_then_email(self) -> None:
        assert suggested_fallbacks(SendErrorCode.RECIPIENT_NOT_VERIFIED) == [
            "sms",
            "email",
        ]

    def test_outside_window_suggests_another_channel(self) -> None:
        assert "sms" in suggested_fallbacks(SendErrorCode.OUTSIDE_MESSAGING_WINDOW)

    def test_consent_and_credential_errors_suggest_nothing(self) -> None:
        for code in (
            SendErrorCode.OPTED_OUT,
            SendErrorCode.AUTH_FAILED,
            SendErrorCode.CONFIG_MISSING,
        ):
            assert suggested_fallbacks(code) == [], code

    def test_fallbacks_are_never_returned_as_a_shared_mutable_list(self) -> None:
        first = suggested_fallbacks(SendErrorCode.RECIPIENT_NOT_VERIFIED)
        first.append("mutated")
        second = suggested_fallbacks(SendErrorCode.RECIPIENT_NOT_VERIFIED)
        assert "mutated" not in second


class TestProviderCodeMapping:
    """Raw provider codes -> normalized codes."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (131030, SendErrorCode.RECIPIENT_NOT_VERIFIED),
            (131047, SendErrorCode.OUTSIDE_MESSAGING_WINDOW),
            (132001, SendErrorCode.TEMPLATE_NOT_FOUND),
            (132000, SendErrorCode.TEMPLATE_PARAM_MISMATCH),
            (190, SendErrorCode.AUTH_FAILED),
            (130429, SendErrorCode.RATE_LIMITED),
            (133010, SendErrorCode.INVALID_RECIPIENT),
            (131042, SendErrorCode.PROVIDER_UNAVAILABLE),
        ],
    )
    def test_meta_codes(self, raw: int, expected: SendErrorCode) -> None:
        assert META_ERROR_CODES[raw] is expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (63018, SendErrorCode.RECIPIENT_NOT_VERIFIED),
            (63016, SendErrorCode.OUTSIDE_MESSAGING_WINDOW),
            (63019, SendErrorCode.OPTED_OUT),
            (21610, SendErrorCode.OPTED_OUT),
            (21211, SendErrorCode.INVALID_RECIPIENT),
            (20429, SendErrorCode.RATE_LIMITED),
            (20003, SendErrorCode.AUTH_FAILED),
            (30001, SendErrorCode.PROVIDER_UNAVAILABLE),
            (21606, SendErrorCode.CONFIG_MISSING),
        ],
    )
    def test_twilio_codes(self, raw: int, expected: SendErrorCode) -> None:
        assert TWILIO_ERROR_CODES[raw] is expected

    def test_tables_only_contain_real_codes(self) -> None:
        for table in (META_ERROR_CODES, TWILIO_ERROR_CODES):
            for code in table.values():
                assert isinstance(code, SendErrorCode)

    @pytest.mark.parametrize(
        "status,expected",
        [
            (400, SendErrorCode.INVALID_REQUEST),
            (401, SendErrorCode.AUTH_FAILED),
            (403, SendErrorCode.AUTH_FAILED),
            (404, SendErrorCode.INVALID_REQUEST),
            (429, SendErrorCode.RATE_LIMITED),
            (500, SendErrorCode.PROVIDER_UNAVAILABLE),
            (503, SendErrorCode.PROVIDER_UNAVAILABLE),
        ],
    )
    def test_http_status_fallback(self, status: int, expected: SendErrorCode) -> None:
        assert from_http_status(status) is expected

    def test_connection_failure_maps_to_network_error(self) -> None:
        assert from_http_status(0) is SendErrorCode.NETWORK_ERROR

    def test_unclassified_status_is_unknown(self) -> None:
        # Redirects and informational responses are not failures we understand.
        assert from_http_status(302) is SendErrorCode.UNKNOWN
        assert from_http_status(600) is SendErrorCode.UNKNOWN
