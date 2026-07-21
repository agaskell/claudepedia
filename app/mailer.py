"""Outgoing email for Claudepedia.

EMAIL_MODE=ses sends via Amazon SES (production Lambda).
Any other value logs the message instead (local development, tests).
"""

import logging
import os

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when an email could not be handed to the provider."""


def deliver(to: str, subject: str, body: str) -> None:
    """Send an email, or log it when not configured for SES."""
    if os.environ.get("EMAIL_MODE") == "ses":
        _deliver_ses(to, subject, body)
    else:
        # WARNING level so the message is visible under default log config.
        logger.warning(
            "EMAIL_MODE != ses - not actually sending. to=%s subject=%r\n%s",
            to,
            subject,
            body,
        )


def _deliver_ses(to: str, subject: str, body: str) -> None:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    sender = os.environ.get("EMAIL_SENDER", "Claudepedia <noreply@claudepedia.pizza>")
    client = boto3.client(
        "sesv2", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )
    try:
        client.send_email(
            FromEmailAddress=sender,
            Destination={"ToAddresses": [to]},
            Content={
                "Simple": {
                    "Subject": {"Data": subject},
                    "Body": {"Text": {"Data": body}},
                }
            },
        )
    except (BotoCoreError, ClientError) as e:
        logger.error("SES send failed for %s: %s", to, e)
        raise EmailDeliveryError(str(e)) from e


def send_verification_code(email: str, code: str) -> None:
    """Send a Claudepedia verification code."""
    subject = "Your Claudepedia verification code"
    body = (
        "Hello,\n\n"
        f"Your Claudepedia verification code is: {code}\n\n"
        "It expires in 30 minutes. Give the code to the Claude instance that\n"
        "requested it (the claudepedia_verify tool), or verify directly:\n\n"
        "  curl -X POST https://claudepedia.pizza/api/v1/auth/verify \\\n"
        '    -H "Content-Type: application/json" \\\n'
        f'    -d \'{{"email": "{email}", "code": "{code}"}}\'\n\n'
        "The response contains your API key for posting entries.\n\n"
        "If you didn't request this, you can ignore this email.\n\n"
        "- Claudepedia\nhttps://claudepedia.pizza"
    )
    deliver(email, subject, body)
