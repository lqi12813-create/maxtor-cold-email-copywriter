#!/usr/bin/env python3
"""Send Maxtor outreach emails through SMTP.

The default mode is a dry run. Use --send only after the recipient, subject,
body, and attachments have been reviewed.
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path


DEFAULT_HOST = "smtp.126.com"
DEFAULT_PORT = 465
DEFAULT_FROM = "zsmaxtor@126.com"
DEFAULT_FROM_NAME = "Maxtor Thermal Solutions"


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def split_addresses(values: list[str] | None) -> list[str]:
    addresses: list[str] = []
    for value in values or []:
        for part in value.replace(";", ",").split(","):
            item = part.strip()
            if item:
                addresses.append(item)
    return addresses


def read_text(value: str | None, file_path: str | None, label: str) -> str | None:
    if value and file_path:
        raise SystemExit(f"Use either --{label} or --{label}-file, not both.")
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    return value


def add_attachments(message: EmailMessage, attachment_paths: list[str] | None) -> None:
    for attachment in attachment_paths or []:
        path = Path(attachment)
        if not path.is_file():
            raise SystemExit(f"Attachment not found: {path}")

        mime_type, encoding = mimetypes.guess_type(path)
        if mime_type is None or encoding is not None:
            mime_type = "application/octet-stream"
        maintype, subtype = mime_type.split("/", 1)
        message.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )


def build_message(args: argparse.Namespace, config: dict[str, object]) -> EmailMessage:
    to_addresses = split_addresses(args.to)
    cc_addresses = split_addresses(args.cc)
    bcc_addresses = split_addresses(args.bcc)

    if not to_addresses:
        raise SystemExit("At least one --to address is required.")
    if not args.subject:
        raise SystemExit("--subject is required.")

    text_body = read_text(args.body, args.body_file, "body")
    html_body = read_text(None, args.html_file, "html")
    if not text_body and not html_body:
        raise SystemExit("Use --body, --body-file, or --html-file.")

    from_email = args.from_email or str(config["from_email"])
    from_name = args.from_name or str(config["from_name"])
    reply_to = args.reply_to or str(config.get("reply_to") or "")

    message = EmailMessage()
    message["From"] = formataddr((from_name, from_email))
    message["To"] = ", ".join(to_addresses)
    if cc_addresses:
        message["Cc"] = ", ".join(cc_addresses)
    if reply_to:
        message["Reply-To"] = reply_to
    message["Subject"] = args.subject
    message["Message-ID"] = make_msgid(domain=from_email.split("@")[-1])

    if text_body:
        message.set_content(text_body)
    else:
        message.set_content("Please view this email in an HTML-compatible client.")
    if html_body:
        message.add_alternative(html_body, subtype="html")

    add_attachments(message, args.attach)
    message._maxtor_all_recipients = to_addresses + cc_addresses + bcc_addresses  # type: ignore[attr-defined]
    return message


def smtp_connect(config: dict[str, object]):
    host = str(config["host"])
    port = int(config["port"])
    timeout = int(config["timeout"])
    use_ssl = bool(config["ssl"])
    use_starttls = bool(config["starttls"])

    if use_ssl:
        return smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())

    smtp = smtplib.SMTP(host, port, timeout=timeout)
    if use_starttls:
        smtp.starttls(context=ssl.create_default_context())
    return smtp


def login(smtp, config: dict[str, object]) -> None:
    username = str(config.get("username") or "")
    password = str(config.get("password") or "")
    if not username or not password:
        raise SystemExit(
            "SMTP credentials are missing. Put SMTP_USERNAME and SMTP_PASSWORD "
            "in .env. Use a 126 authorization code, not the mailbox login password."
        )
    smtp.login(username, password)


def send_message(message: EmailMessage, config: dict[str, object]) -> None:
    with smtp_connect(config) as smtp:
        login(smtp, config)
        smtp.send_message(message, to_addrs=message._maxtor_all_recipients)  # type: ignore[attr-defined]


def check_login(config: dict[str, object]) -> None:
    with smtp_connect(config) as smtp:
        login(smtp, config)
    print(f"SMTP login OK: {config['username']} via {config['host']}:{config['port']}")


def save_eml(message: EmailMessage, save_path: str) -> None:
    path = Path(save_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(message))
    print(f"Saved EML: {path}")


def get_config(args: argparse.Namespace) -> dict[str, object]:
    return {
        "host": args.smtp_host or os.getenv("SMTP_HOST", DEFAULT_HOST),
        "port": args.smtp_port or int(os.getenv("SMTP_PORT", str(DEFAULT_PORT))),
        "ssl": parse_bool(os.getenv("SMTP_SSL"), True),
        "starttls": parse_bool(os.getenv("SMTP_STARTTLS"), False),
        "username": os.getenv("SMTP_USERNAME") or os.getenv("SMTP_USER") or os.getenv("MAIL_USER") or "",
        "password": os.getenv("SMTP_PASSWORD") or os.getenv("SMTP_PASS") or os.getenv("MAIL_PASSWORD") or "",
        "from_email": os.getenv("SMTP_FROM") or os.getenv("MAIL_FROM") or DEFAULT_FROM,
        "from_name": os.getenv("SMTP_FROM_NAME") or DEFAULT_FROM_NAME,
        "reply_to": os.getenv("SMTP_REPLY_TO") or "",
        "timeout": int(os.getenv("SMTP_TIMEOUT", "30")),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send Maxtor outreach email via SMTP.")
    parser.add_argument("--env", default=".env", help="Path to local env file. Default: .env")
    parser.add_argument("--to", action="append", help="Recipient email. Can be repeated or comma-separated.")
    parser.add_argument("--cc", action="append", help="CC email. Can be repeated or comma-separated.")
    parser.add_argument("--bcc", action="append", help="BCC email. Can be repeated or comma-separated.")
    parser.add_argument("--subject", help="Email subject.")
    parser.add_argument("--body", help="Plain-text email body.")
    parser.add_argument("--body-file", help="UTF-8 plain-text body file.")
    parser.add_argument("--html-file", help="UTF-8 HTML body file.")
    parser.add_argument("--attach", action="append", help="Attachment path. Can be repeated.")
    parser.add_argument("--from-email", help="Override From email.")
    parser.add_argument("--from-name", help="Override From display name.")
    parser.add_argument("--reply-to", help="Override Reply-To email.")
    parser.add_argument("--smtp-host", help="Override SMTP host.")
    parser.add_argument("--smtp-port", type=int, help="Override SMTP port.")
    parser.add_argument("--save-eml", help="Save the MIME message to an .eml file.")
    parser.add_argument("--check-login", action="store_true", help="Only verify SMTP login.")
    parser.add_argument("--send", action="store_true", help="Actually send. Without this flag, only dry-run.")
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_env_file(Path(args.env))
    config = get_config(args)

    if args.check_login:
        check_login(config)
        return 0

    message = build_message(args, config)
    if args.save_eml:
        save_eml(message, args.save_eml)

    recipients = ", ".join(message._maxtor_all_recipients)  # type: ignore[attr-defined]
    print(f"From: {message['From']}")
    print(f"To/Cc/Bcc: {recipients}")
    print(f"Subject: {message['Subject']}")

    if not args.send:
        print("Dry run only. Add --send after the email is reviewed and approved.")
        return 0

    send_message(message, config)
    print("Email sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
