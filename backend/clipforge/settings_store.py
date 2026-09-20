"""Read and write user settings in .env (secrets included) without ever echoing a secret back."""

from __future__ import annotations

import os
import re
from pathlib import Path

PUBLIC_KEYS = {
    "CLIPFORGE_SCAN_MODEL", "CLIPFORGE_CURATE_MODEL", "CLIPFORGE_MAX_SCAN_MODEL", "CLIPFORGE_MAX_CURATE_MODEL",
    "MAX_JOB_COST_USD", "ASR_BACKEND", "ASR_MODEL", "LANGUAGE_POLICY", "DEFAULT_LAYOUT", "RENDER_UPSCALE", "ASR_MODEL_NON_ENGLISH", "MAX_SOURCE_HEIGHT", "YTDLP_COOKIES_FROM_BROWSER", "RENDER_WORKERS", "GOOGLE_CLIENT_ID",
}  # fmt: skip
SECRET_KEYS = {"ANTHROPIC_API_KEY", "HF_TOKEN", "GOOGLE_CLIENT_SECRET"}
ENV_FILE = Path(".env")


def _read(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def write_env(updates: dict[str, str], path: Path | None = None) -> None:
    """Set KEY=value lines in .env, preserving comments and other keys; file mode 0600."""
    path = path or ENV_FILE
    for k in updates:
        if k not in PUBLIC_KEYS | SECRET_KEYS:
            raise ValueError(f"{k} is not a setting")
        if re.search(r"[\r\n]", updates[k]):
            raise ValueError("Values cannot contain newlines")
    lines, seen = _read(path), set()
    out = []
    for line in lines:
        m = re.match(r"^([A-Z0-9_]+)=", line)
        if m and m.group(1) in updates:
            out.append(f"{m.group(1)}={updates[m.group(1)]}")
            seen.add(m.group(1))
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    path.write_text("\n".join(out) + "\n")
    path.chmod(0o600)
    for k, v in updates.items():  # new processes read .env; make this process agree too
        os.environ[k] = v


def mask(secret: str | None) -> str | None:
    return None if not secret else ("*" * 8 + secret[-4:] if len(secret) > 8 else "*" * len(secret))


def migrate_env(path: Path | None = None) -> bool:
    """One-time: the old default cap of 1080p made every vertical crop soft. Raise an untouched 1080 to 2160."""
    path = path or ENV_FILE
    lines = _read(path)
    marker = "# clipforge: source cap raised to 2160 (4K) for sharper crops"
    if marker in lines or "MAX_SOURCE_HEIGHT=1080" not in lines:
        return False
    out = [("MAX_SOURCE_HEIGHT=2160" if ln == "MAX_SOURCE_HEIGHT=1080" else ln) for ln in lines] + [
        marker
    ]
    path.write_text("\n".join(out) + "\n")
    path.chmod(0o600)
    return True
