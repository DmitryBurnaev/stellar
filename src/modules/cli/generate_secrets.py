"""Create local Stellar configuration with generated credentials."""

import os
import secrets
from pathlib import Path

ENV_FILE_PATH = Path(".env")
ENV_TEMPLATE_PATH = Path(".env.template")


def main() -> None:
    template = ENV_TEMPLATE_PATH.read_text(encoding="utf-8")
    lines = (
        f"DB_PASSWORD={secrets.token_urlsafe(24)}",
        f"ADMIN_PASSWORD={secrets.token_urlsafe(24)}",
        f"APP_SECRET_KEY={secrets.token_urlsafe(48)}",
    )
    try:
        fd = os.open(ENV_FILE_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(f"{ENV_FILE_PATH} already exists") from None
    with os.fdopen(fd, "w", encoding="utf-8") as config:
        config.write(template.rstrip() + "\n\n" + "\n".join(lines) + "\n")
    print(f"Created {ENV_FILE_PATH} with generated credentials.")


if __name__ == "__main__":
    main()
