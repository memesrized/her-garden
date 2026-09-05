"""Create private runtime configuration without printing credentials."""

import argparse
import getpass
import hashlib
import os
import secrets
from pathlib import Path


def main() -> None:
    """Prompt for a household password and exclusively create a mode-0600 env file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-url", default="http://localhost:8002/garden")
    parser.add_argument("--output", type=Path, default=Path(".env"))
    args = parser.parse_args()
    password = getpass.getpass("Choose a household password (at least 16 characters): ")
    if len(password) < 16:
        raise SystemExit("Use at least 16 characters")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords did not match")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
    content = (
        f"PUBLIC_URL={args.public_url}\nPOSTGRES_PASSWORD={secrets.token_hex(32)}\n"
        f"HOUSEHOLD_PASSWORD_HASH={salt.hex()}:{digest}\n"
    )
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(content)
    print(f"Private configuration created at {args.output}; password was not stored.")


if __name__ == "__main__":
    main()
