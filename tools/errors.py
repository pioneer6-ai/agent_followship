"""
Normalized error taxonomy for outbound message delivery.

The whole point of this module is to translate provider-specific failures
(Meta Graph error codes, Twilio numeric codes, SMTP exceptions, raw HTTP
statuses) into a *small, stable vocabulary* that an LLM can reason about.

The LLM never sees a stack trace. It sees something like:

    {"error_code": "recipient_not_verified",
     "retryable": false,
     "suggested_fallback_channels": ["sms", "email"]}

...which is enough for it to decide "WhatsApp is blocked for this patient,
try email instead" without any human in the loop.

Provider-specific codes are always preserved verbatim in
``ToolResult.provider_code`` / ``error_message`` so nothing is lost.
"""

from __future__ import annotations
from enum import Enum
from typing import Dict, List, Optional


class SendErrorCode(str, Enum):
    """
    Stable, provider-independent failure vocabulary for a send attempt.

    Being a ``str`` subclass means the value serializes straight to JSON
    that the model can read.

    Attributes:
        RECIPIENT_NOT_VERIFIED: The destination is not opted-in / not in the
            sandbox allow-list / has never messaged the business. This is the
            canonical "recipient not verified" case: the *channel* is fine but
            this recipient is not allowed on it yet.
        INVALID_RECIPIENT: Malformed, unreachable or non-existent address.
        OPTED_OUT: Recipient previously sent STOP / revoked consent on that channel.
        OUTSIDE_MESSAGING_WINDOW: Business-initiated free-form message sent
            outside the allowed customer-service window.
        TEMPLATE_NOT_FOUND: Named template is unknown or unapproved.
        TEMPLATE_PARAM_MISMATCH: Wrong number/format of template parameters.
        RATE_LIMITED: Provider throttled us; retry later.
        AUTH_FAILED: Bad/expired credentials or inactive account.
        CONFIG_MISSING: Required local configuration (credentials, sender id) absent.
        PROVIDER_UNAVAILABLE: Provider-side outage or 5xx.
        NETWORK_ERROR: Connection to the provider failed locally.
        INVALID_REQUEST: We built a request the provider rejected as malformed.
        NOT_SUBSCRIBED: The AWS account is not onboarded to the service in the
            configured region (``SubscriptionRequiredException``). The code is
            fine, the account is not: no amount of retrying fixes it.
        UNKNOWN: Anything not otherwise classified.
    """

    RECIPIENT_NOT_VERIFIED = "recipient_not_verified"
    INVALID_RECIPIENT = "invalid_recipient"
    OPTED_OUT = "opted_out"
    OUTSIDE_MESSAGING_WINDOW = "outside_messaging_window"
    TEMPLATE_NOT_FOUND = "template_not_found"
    TEMPLATE_PARAM_MISMATCH = "template_param_mismatch"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    CONFIG_MISSING = "config_missing"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    NETWORK_ERROR = "network_error"
    INVALID_REQUEST = "invalid_request"
    NOT_SUBSCRIBED = "not_subscribed"
    UNKNOWN = "unknown"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


#: Errors where retrying the *same* channel later may succeed.
RETRYABLE_ERRORS = frozenset(
    {
        SendErrorCode.RATE_LIMITED,
        SendErrorCode.PROVIDER_UNAVAILABLE,
        SendErrorCode.NETWORK_ERROR,
    }
)

#: Errors where switching to a *different* channel may succeed.
#: Empty means: retrying another channel will not help, fix the root cause
#: (or hand the case to a human) instead.
_FALLBACK_CHANNELS: Dict[SendErrorCode, List[str]] = {
    SendErrorCode.RECIPIENT_NOT_VERIFIED: ["sms", "email"],
    SendErrorCode.INVALID_RECIPIENT: ["email"],
    SendErrorCode.OUTSIDE_MESSAGING_WINDOW: ["sms", "email"],
    SendErrorCode.TEMPLATE_NOT_FOUND: [],
    SendErrorCode.TEMPLATE_PARAM_MISMATCH: [],
    SendErrorCode.OPTED_OUT: [],
    SendErrorCode.RATE_LIMITED: [],
    SendErrorCode.AUTH_FAILED: [],
    SendErrorCode.CONFIG_MISSING: [],
    SendErrorCode.PROVIDER_UNAVAILABLE: [],
    SendErrorCode.NETWORK_ERROR: [],
    SendErrorCode.INVALID_REQUEST: [],
    SendErrorCode.NOT_SUBSCRIBED: [],
    SendErrorCode.UNKNOWN: [],
}

_HINTS: Dict[SendErrorCode, str] = {
    SendErrorCode.RECIPIENT_NOT_VERIFIED: (
        "The recipient has not opted in / is not in the provider allow-list "
        "(WhatsApp sandbox or Meta allowed-list). Sending again on this channel "
        "will keep failing: switch to another channel the patient has consented to."
    ),
    SendErrorCode.INVALID_RECIPIENT: (
        "The address itself is unusable. Verify the number/address on file; "
        "for WhatsApp the country code must be present (E.164)."
    ),
    SendErrorCode.OPTED_OUT: (
        "The recipient withdrew consent on this channel. Do not message this "
        "channel again; a different consented channel or staff contact is required."
    ),
    SendErrorCode.OUTSIDE_MESSAGING_WINDOW: (
        "Business-initiated free-form messages require an approved template once "
        "the customer-service window has closed. Resend using a template, or use "
        "another channel."
    ),
    SendErrorCode.TEMPLATE_NOT_FOUND: (
        "The template name is not in the local catalogue or is not approved in the "
        "provider account. Use one of the supported template names."
    ),
    SendErrorCode.TEMPLATE_PARAM_MISMATCH: (
        "The number of parameters does not match what the template expects. "
        "Fix the parameter list and retry."
    ),
    SendErrorCode.RATE_LIMITED: (
        "Provider throttled this send. Wait before retrying the same channel."
    ),
    SendErrorCode.AUTH_FAILED: (
        "Credentials or account are invalid. This is an operational failure the "
        "agent cannot fix: escalate to staff."
    ),
    SendErrorCode.CONFIG_MISSING: (
        "Local configuration for this channel is incomplete. Running in dry-run "
        "mode sends nothing. Escalate to staff if a real send was required."
    ),
    SendErrorCode.PROVIDER_UNAVAILABLE: (
        "The provider is failing on its side. Retry the same channel later."
    ),
    SendErrorCode.NETWORK_ERROR: (
        "Could not reach the provider from this host. Retry the same channel later."
    ),
    SendErrorCode.INVALID_REQUEST: (
        "The request was rejected as malformed. Correct the arguments and retry."
    ),
    SendErrorCode.NOT_SUBSCRIBED: (
        "The AWS account is not subscribed to this messaging service in the "
        "configured region, so nothing can be delivered on this channel. "
        "Credentials are fine; an operator must onboard the account. The agent "
        "cannot fix this: use another delivery channel or escalate to staff."
    ),
    SendErrorCode.UNKNOWN: (
        "Unclassified failure. Inspect provider_code and error_message; consider "
        "another channel or escalation."
    ),
}


def is_retryable(code: SendErrorCode) -> bool:
    """Return True when retrying the same channel later may succeed."""
    return code in RETRYABLE_ERRORS


def suggested_fallbacks(code: SendErrorCode) -> List[str]:
    """Return channel names worth trying after this failure (may be empty)."""
    return list(_FALLBACK_CHANNELS.get(code, []))


def hint_for(code: SendErrorCode) -> str:
    """Return an actionable, LLM-readable explanation of the error code."""
    return _HINTS.get(code, _HINTS[SendErrorCode.UNKNOWN])


# --------------------------------------------------------------------------
# Provider code -> normalized code
# --------------------------------------------------------------------------

#: Meta WhatsApp Cloud API / Graph API error codes.
#: See https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes
META_ERROR_CODES: Dict[int, SendErrorCode] = {
    4: SendErrorCode.RATE_LIMITED,               # application request limit reached
    100: SendErrorCode.INVALID_REQUEST,          # invalid parameter
    102: SendErrorCode.AUTH_FAILED,              # session key invalid
    190: SendErrorCode.AUTH_FAILED,              # access token expired
    368: SendErrorCode.RATE_LIMITED,             # temporarily blocked for policy violations
    80007: SendErrorCode.RATE_LIMITED,           # rate limit issues
    130429: SendErrorCode.RATE_LIMITED,          # rate limit hit
    131009: SendErrorCode.INVALID_REQUEST,       # parameter value not valid
    131026: SendErrorCode.INVALID_RECIPIENT,     # message undeliverable
    131030: SendErrorCode.RECIPIENT_NOT_VERIFIED,  # recipient not in allowed list
    131031: SendErrorCode.AUTH_FAILED,           # account locked
    131042: SendErrorCode.PROVIDER_UNAVAILABLE,  # business eligibility / payment issue
    131047: SendErrorCode.OUTSIDE_MESSAGING_WINDOW,   # re-engagement message
    131048: SendErrorCode.RATE_LIMITED,          # spam rate limit hit
    131051: SendErrorCode.INVALID_REQUEST,       # unsupported message type
    131056: SendErrorCode.RATE_LIMITED,          # pair rate limit hit
    132000: SendErrorCode.TEMPLATE_PARAM_MISMATCH,  # param count mismatch
    132001: SendErrorCode.TEMPLATE_NOT_FOUND,    # template does not exist
    132005: SendErrorCode.TEMPLATE_PARAM_MISMATCH,  # translated text too long
    132007: SendErrorCode.TEMPLATE_NOT_FOUND,    # template paused for policy violation
    132012: SendErrorCode.TEMPLATE_PARAM_MISMATCH,  # parameter format mismatch
    132015: SendErrorCode.TEMPLATE_NOT_FOUND,    # template paused
    132016: SendErrorCode.TEMPLATE_NOT_FOUND,    # template disabled
    133010: SendErrorCode.INVALID_RECIPIENT,     # phone number not registered
}

#: Twilio REST API error codes.
#: See https://www.twilio.com/docs/api/errors
TWILIO_ERROR_CODES: Dict[int, SendErrorCode] = {
    20003: SendErrorCode.AUTH_FAILED,            # authentication error / permission denied
    20005: SendErrorCode.AUTH_FAILED,            # account not active
    20429: SendErrorCode.RATE_LIMITED,           # too many requests
    21211: SendErrorCode.INVALID_RECIPIENT,      # invalid 'To' number
    21214: SendErrorCode.INVALID_RECIPIENT,      # 'To' number is not a valid mobile number
    21606: SendErrorCode.CONFIG_MISSING,         # 'From' not verified for account
    21608: SendErrorCode.CONFIG_MISSING,         # 'From' not verified
    21610: SendErrorCode.OPTED_OUT,              # unsubscribed recipient (STOP)
    21612: SendErrorCode.INVALID_RECIPIENT,      # 'To' number not reachable
    21614: SendErrorCode.INVALID_RECIPIENT,      # 'To' number is not a valid mobile number
    30001: SendErrorCode.PROVIDER_UNAVAILABLE,   # queue overflow
    30002: SendErrorCode.AUTH_FAILED,            # account suspended
    30003: SendErrorCode.PROVIDER_UNAVAILABLE,   # unreachable destination handset
    30004: SendErrorCode.RATE_LIMITED,           # message blocked
    30005: SendErrorCode.INVALID_RECIPIENT,      # unknown destination handset
    30006: SendErrorCode.INVALID_RECIPIENT,      # landline or unreachable carrier
    30007: SendErrorCode.PROVIDER_UNAVAILABLE,   # carrier violation / filtered
    63003: SendErrorCode.CONFIG_MISSING,         # channel not found for 'From'
    63007: SendErrorCode.CONFIG_MISSING,         # channel could not find 'From'
    63016: SendErrorCode.OUTSIDE_MESSAGING_WINDOW,  # outside 24h window
    63018: SendErrorCode.RECIPIENT_NOT_VERIFIED,  # channel could not find 'To' (not joined sandbox)
    63019: SendErrorCode.OPTED_OUT,              # blacklisted From/To pair
    63024: SendErrorCode.OUTSIDE_MESSAGING_WINDOW,
    63032: SendErrorCode.OPTED_OUT,              # recipient opted out of WhatsApp
}


def from_http_status(status_code: int) -> SendErrorCode:
    """
    Best-effort classification from a bare HTTP status.

    Used as a fallback when the response body carries no recognizable
    provider error code.

    Args:
        status_code: HTTP status (``0`` means the connection itself failed)

    Returns:
        Normalized error code
    """
    if status_code == 0:
        return SendErrorCode.NETWORK_ERROR
    if status_code in (401, 403):
        return SendErrorCode.AUTH_FAILED
    if status_code == 429:
        return SendErrorCode.RATE_LIMITED
    if 500 <= status_code < 600:
        return SendErrorCode.PROVIDER_UNAVAILABLE
    if 400 <= status_code < 500:
        return SendErrorCode.INVALID_REQUEST
    return SendErrorCode.UNKNOWN


#: boto3/botocore error codes shared by AWS End User Messaging SMS and SES.
#: Keyed by ``ClientError.response["Error"]["Code"]`` (a string), which is why
#: this table is separate from the numeric Meta/Twilio ones.
#: See https://docs.aws.amazon.com/sms-voice/latest/userguide/notifications.html
#: and https://docs.aws.amazon.com/ses/latest/dg/transactional-email.html
AWS_ERROR_CODES: Dict[str, SendErrorCode] = {
    # Account/region not onboarded for the service.
    "SubscriptionRequiredException": SendErrorCode.NOT_SUBSCRIBED,
    # SES sandbox: "Email address is not verified. The following identities
    # failed the check in region ...". The channel is fine, the recipient is
    # not allowed on it yet.
    "MessageRejected": SendErrorCode.RECIPIENT_NOT_VERIFIED,
    # Throttling / quota.
    "ThrottlingException": SendErrorCode.RATE_LIMITED,
    "Throttling": SendErrorCode.RATE_LIMITED,
    "TooManyRequestsException": SendErrorCode.RATE_LIMITED,
    "LimitExceededException": SendErrorCode.RATE_LIMITED,
    # Credentials / permissions.
    "AccessDeniedException": SendErrorCode.AUTH_FAILED,
    "AccessDenied": SendErrorCode.AUTH_FAILED,
    "UnrecognizedClientException": SendErrorCode.AUTH_FAILED,
    "InvalidClientTokenId": SendErrorCode.AUTH_FAILED,
    "ExpiredTokenException": SendErrorCode.AUTH_FAILED,
    "SignatureDoesNotMatch": SendErrorCode.AUTH_FAILED,
    "AccountSuspendedException": SendErrorCode.AUTH_FAILED,
    # Request shape.
    "ValidationException": SendErrorCode.INVALID_REQUEST,
    "InvalidParameterValue": SendErrorCode.INVALID_REQUEST,
    "ParamValidationError": SendErrorCode.INVALID_REQUEST,
    "BadRequestException": SendErrorCode.INVALID_REQUEST,
    # Provider-side outage.
    "ServiceUnavailable": SendErrorCode.PROVIDER_UNAVAILABLE,
    "InternalServiceError": SendErrorCode.PROVIDER_UNAVAILABLE,
    "InternalServiceErrorException": SendErrorCode.PROVIDER_UNAVAILABLE,
    "InternalFailure": SendErrorCode.PROVIDER_UNAVAILABLE,
    # Local connectivity (botocore raises these instead of ClientError).
    "EndpointConnectionError": SendErrorCode.NETWORK_ERROR,
    "ConnectTimeoutError": SendErrorCode.NETWORK_ERROR,
    "ReadTimeoutError": SendErrorCode.NETWORK_ERROR,
    # Local setup problems (botocore exception names, not provider codes).
    "NoCredentialsError": SendErrorCode.CONFIG_MISSING,
    "PartialCredentialsError": SendErrorCode.CONFIG_MISSING,
    "InvalidRegionError": SendErrorCode.CONFIG_MISSING,
    "UnknownServiceError": SendErrorCode.CONFIG_MISSING,
}


def from_boto_error(code: Optional[str]) -> SendErrorCode:
    """
    Classify an AWS SDK error code into the normalized vocabulary.

    Args:
        code: ``ClientError.response["Error"]["Code"]``, or a botocore
            exception class name. Case is ignored; unknown values map to
            :attr:`SendErrorCode.UNKNOWN` so nothing is ever silently dropped.

    Returns:
        Normalized error code.
    """
    if not code:
        return SendErrorCode.UNKNOWN
    raw = str(code).strip()
    if raw in AWS_ERROR_CODES:
        return AWS_ERROR_CODES[raw]
    lowered = raw.lower()
    for known, mapped in AWS_ERROR_CODES.items():
        if known.lower() == lowered:
            return mapped
    if "throttl" in lowered or "toomanyrequests" in lowered:
        return SendErrorCode.RATE_LIMITED
    if "timeout" in lowered:
        return SendErrorCode.NETWORK_ERROR
    return SendErrorCode.UNKNOWN
