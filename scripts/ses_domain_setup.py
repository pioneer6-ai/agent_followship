#!/usr/bin/env python3
"""Prepare an owned domain for SES sending and check its verification status.

Sending from an address you own is the fix for mail landing in spam: SES signs
it with your domain's DKIM key, so the message carries a signature that matches
the From domain and DMARC alignment holds.

Usage:
    python scripts/ses_domain_setup.py clinic.example.com --create
    python scripts/ses_domain_setup.py clinic.example.com --check

Requires boto3 and SES credentials (see the credentials caveat in README.md).
"""

from __future__ import annotations

import argparse
import sys

MAIL_FROM_SUBDOMAIN = "ses"
RULE = "-" * 72


def _sesv2(region: str):
    import boto3

    return boto3.client("sesv2", region_name=region)


def _records(domain: str, region: str, tokens: list[str]) -> list[tuple[str, str, str, str]]:
    """(type, name, value, importance) tuples in the order they should be added."""
    records = [
        ("CNAME", f"{token}._domainkey.{domain}", f"{token}.dkim.amazonses.com", "required")
        for token in tokens
    ]
    records.append(("TXT", domain, "v=spf1 include:amazonses.com ~all", "recommended"))
    records.append(
        (
            "TXT",
            f"_dmarc.{domain}",
            f"v=DMARC1; p=none; rua=mailto:dmarc@{domain.split('.', 1)[-1]}",
            "recommended",
        )
    )
    records.append(
        (
            "MX",
            f"{MAIL_FROM_SUBDOMAIN}.{domain}",
            f"10 feedback-smtp.{region}.amazonses.com",
            "optional",
        )
    )
    records.append(
        (
            "TXT",
            f"{MAIL_FROM_SUBDOMAIN}.{domain}",
            "v=spf1 include:amazonses.com ~all",
            "optional",
        )
    )
    return records


def _print_records(records: list[tuple[str, str, str, str]]) -> None:
    print("\nAdd these DNS records at your DNS provider:\n")
    width = max(len(name) for _, name, _, _ in records)
    for kind, name, value, importance in records:
        print(f"  [{importance:11}] {kind:<5} {name:<{width}}  ->  {value}")
    print(
        "\nThe three CNAMEs are what SES reads to prove you control the domain."
        "\nThe SPF and DMARC TXT records are not read by SES but Gmail/Outlook"
        "\nscore them, so add them too. The MX/TXT pair is only needed if you"
        "\nset a custom MAIL FROM domain."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain", help="domain to verify, e.g. clinic.example.com")
    parser.add_argument("--region", default="ap-southeast-1")
    parser.add_argument(
        "--create",
        action="store_true",
        help="create the SES domain identity (idempotent) before printing records",
    )
    parser.add_argument(
        "--check", action="store_true", help="only report the current verification status"
    )
    args = parser.parse_args()

    try:
        client = _sesv2(args.region)
    except ImportError:
        print("boto3 is not installed: .venv/bin/pip install boto3", file=sys.stderr)
        return 2

    from botocore.exceptions import ClientError

    if args.create:
        try:
            client.create_email_identity(EmailIdentity=args.domain)
            print(f"created SES email identity {args.domain}")
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "AlreadyExistsException":
                print(f"SES email identity {args.domain} already exists")
            else:
                print(f"create failed: {code}: {exc.response['Error']['Message']}", file=sys.stderr)
                return 1

    try:
        identity = client.get_email_identity(EmailIdentity=args.domain)
    except ClientError as exc:
        print(f"lookup failed: {exc.response['Error']['Message']}", file=sys.stderr)
        return 1

    dkim = identity.get("DkimAttributes", {})
    sending = identity.get("VerifiedForSendingStatus")
    print(RULE)
    print(f"identity            : {identity.get('EmailIdentity', args.domain)}")
    print(f"region              : {args.region}")
    print(f"verified for sending: {sending}")
    print(
        f"dkim status         : {dkim.get('Status')}  "
        f"(signing enabled: {dkim.get('SigningEnabled')})"
    )
    print(RULE)

    if not args.check:
        _print_records(_records(args.domain, args.region, dkim.get("Tokens", [])))

    if sending:
        print("\nDomain is verified. Point the agent at it:\n")
        print(f"  AWS_SES_SOURCE=reminders@{args.domain}")
        print("  AWS_EMAIL_ALLOWED_ADDRESSES=<verified test address>")
        return 0
    print("\nNot verified yet. DNS changes can take a while to propagate; re-run with --check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
