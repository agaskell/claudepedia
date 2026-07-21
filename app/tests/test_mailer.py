"""Tests for the verification mailer."""


def test_send_verification_code_formats_email(monkeypatch):
    import mailer

    captured = {}

    def fake_deliver(to: str, subject: str, body: str) -> None:
        captured.update(to=to, subject=subject, body=body)

    monkeypatch.setattr(mailer, "deliver", fake_deliver)
    mailer.send_verification_code("x@example.com", "ABCD2345")

    assert captured["to"] == "x@example.com"
    assert "Claudepedia" in captured["subject"]
    assert "ABCD2345" in captured["body"]


def test_log_mode_send_does_not_raise():
    """Without EMAIL_MODE=ses the mailer logs instead of sending."""
    import mailer

    mailer.send_verification_code("x@example.com", "ABCD2345")
