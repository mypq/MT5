import argparse
import asyncio
import re
import secrets
import sys

from app.core.config import settings
from app.core.device_registry import (
    DeviceRegistry,
    DeviceRegistryError,
)
from app.core.device_secret_crypto import (
    DeviceSecretCipher,
    DeviceSecretEncryptionError,
)


SOURCE_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9_.:-]{1,128}$"
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Register a trusted GOLD BOT MT5 data source."
        )
    )

    parser.add_argument(
        "--source-id",
        required=True,
        help=(
            "Unique device identifier, for example MT5-DEMO-01."
        ),
    )

    return parser.parse_args()


async def register_device_async(source_id: str) -> int:
    database_url = settings.DATABASE_URL.get_secret_value()
    encryption_key = (
        settings.DEVICE_SECRET_ENCRYPTION_KEY.get_secret_value()
    )

    cipher = DeviceSecretCipher(encryption_key)

    registry = DeviceRegistry(
        database_url=database_url,
        cipher=cipher,
    )

    # Ensure the registry table and indexes exist.
    await registry.initialize()

    # Generate a cryptographically secure secret.
    # It is never passed through command-line arguments.
    device_secret = secrets.token_urlsafe(48)

    encrypted_secret = cipher.encrypt(
        source_id=source_id,
        secret=device_secret,
    )

    # Use the public registration method so that the
    # encrypted secret is authenticated before storage.
    registered = await registry.register_device(
        source_id=source_id,
        encrypted_secret=encrypted_secret,
    )

    if not registered:
        print(
            "ERROR: This source-id is already registered. "
            "No existing device was changed.",
            file=sys.stderr,
        )
        return 1

    print()
    print("Device registered successfully.")
    print(f"Source ID: {source_id}")
    print()
    print("DEVICE SECRET (shown only once):")
    print(device_secret)
    print()
    print(
        "Copy this secret into the trusted MT5 EA "
        "configuration now."
    )
    print(
        "Store it securely. Do not commit it to GitHub, "
        "send it in logs, or share it publicly."
    )
    print(
        "If this secret is lost, revoke the device and "
        "register a new source ID."
    )

    return 0


def register_device(source_id: str) -> int:
    if not SOURCE_ID_PATTERN.fullmatch(source_id):
        print(
            "ERROR: source-id must contain only "
            "letters, numbers, underscores, dots, "
            "colons, or hyphens; maximum 128 characters.",
            file=sys.stderr,
        )
        return 2

    try:
        return asyncio.run(
            register_device_async(source_id)
        )

    except (
        DeviceSecretEncryptionError,
        DeviceRegistryError,
        ValueError,
    ) as exc:
        print(
            f"ERROR: Device registration failed: {exc}",
            file=sys.stderr,
        )
        return 1


def main() -> int:
    arguments = parse_arguments()
    return register_device(arguments.source_id)


if __name__ == "__main__":
    raise SystemExit(main())
