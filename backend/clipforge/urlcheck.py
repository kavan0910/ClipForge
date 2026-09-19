"""URL validation for the link input: http/https only, no private-network targets (SSRF)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from clipforge.errors import DownloadError


def validate_url(url: str, allow_private: bool = False) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DownloadError(
            "Only http and https links are supported.",
            "Paste a full link that starts with https://",
        )
    if parsed.username or parsed.password:
        raise DownloadError("Links with embedded credentials are not accepted.", "Remove them.")
    if not allow_private:
        try:
            infos = socket.getaddrinfo(parsed.hostname, None)
        except socket.gaierror as e:
            raise DownloadError(
                f"Could not resolve {parsed.hostname}.", "Check the link and your connection."
            ) from e
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                raise DownloadError(
                    "That link points to a private or local network address.",
                    "Only public links are allowed by default.",
                )
    return url
