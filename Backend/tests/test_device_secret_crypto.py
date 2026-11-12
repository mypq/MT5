
import base64

import pytest

from app.core.device_secret_crypto import (
    DeviceSecretCipher,
    DeviceSecretEncryptionError,
)


KEY = "ab" * 32
OTHER_KEY = "cd" * 32
SOURCE_ID = "MT5-DEMO-01"
SECRET = "gold-bot-device-secret-" + ("x" * 40)


def make_cipher(key=KEY):
    return DeviceSecretCipher(key)


def encrypt_secret(
    cipher=None,
    source_id=SOURCE_ID,
    secret=SECRET,
):
    cipher = cipher or make_cipher()
    return cipher.encrypt(
        source_id=source_id,
        secret=secret,
    )


def test_encryption_uses_versioned_token():
    token = encrypt_secret()

    assert token.startswith("v1:")
    assert len(token) <= 6000


def test_each_encryption_uses_a_fresh_nonce():
    cipher = make_cipher()

    first = encrypt_secret(cipher)
    second = encrypt_secret(cipher)

    assert first != second


def test_bytes_secret_round_trip():
    cipher = make_cipher()
    secret = b"x" * 64

    token = encrypt_secret(cipher, secret=secret)

    assert cipher.decrypt(
        source_id=SOURCE_ID,
        encrypted_secret=token,
    ) == secret


def test_maximum_secret_length_is_accepted():
    cipher = make_cipher()
    secret = b"x" * 4096

    token = encrypt_secret(cipher, secret=secret)

    assert cipher.decrypt(
        source_id=SOURCE_ID,
        encrypted_secret=token,
    ) == secret


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "x" * 31,
        b"",
        b"x" * 31,
        b"x" * 4097,
        "x" * 4097,
        None,
        123,
        bytearray(b"x" * 40),
    ],
)
def test_invalid_secret_values_are_rejected(secret):
    with pytest.raises(DeviceSecretEncryptionError):
        encrypt_secret(secret=secret)


@pytest.mark.parametrize(
    "source_id",
    [
        "",
        " ",
        "\n",
        "DEVICE\nATTACK",
        "x" * 129,
        None,
        123,
    ],
)
def test_invalid_source_ids_are_rejected(source_id):
    cipher = make_cipher()

    with pytest.raises(DeviceSecretEncryptionError):
        cipher.encrypt(
            source_id=source_id,
            secret=SECRET,
        )


def test_source_id_surrounding_spaces_are_normalized():
    cipher = make_cipher()

    token = encrypt_secret(
        cipher,
        source_id="  MT5-DEMO-01  ",
    )

    assert cipher.decrypt(
        source_id=SOURCE_ID,
        encrypted_secret=token,
    ) == SECRET.encode("utf-8")


def test_wrong_encryption_key_is_rejected():
    token = encrypt_secret()

    wrong_cipher = make_cipher(OTHER_KEY)

    with pytest.raises(DeviceSecretEncryptionError):
        wrong_cipher.decrypt(
            source_id=SOURCE_ID,
            encrypted_secret=token,
        )


def test_modified_ciphertext_is_rejected():
    token = encrypt_secret()

    encoded = token[3:]
    payload = bytearray(
        base64.urlsafe_b64decode(
            encoded + "=" * (-len(encoded) % 4)
        )
    )

    payload[-1] ^= 1

    modified = "v1:" + base64.urlsafe_b64encode(
        payload
    ).rstrip(b"=").decode("ascii")

    with pytest.raises(DeviceSecretEncryptionError):
        make_cipher().decrypt(
            source_id=SOURCE_ID,
            encrypted_secret=modified,
        )


@pytest.mark.parametrize(
    "token",
    [
        "",
        "v2:invalid",
        "invalid",
        "v1:",
        "v1:%%%",
        "v1:abc",
        "v1:" + ("a" * 6001),
        None,
        123,
    ],
)
def test_malformed_encrypted_tokens_are_rejected(token):
    with pytest.raises(DeviceSecretEncryptionError):
        make_cipher().decrypt(
            source_id=SOURCE_ID,
            encrypted_secret=token,
        )


def test_incomplete_encrypted_payload_is_rejected():
    incomplete = base64.urlsafe_b64encode(
        b"x" * 20
    ).rstrip(b"=").decode("ascii")

    with pytest.raises(DeviceSecretEncryptionError):
        make_cipher().decrypt(
            source_id=SOURCE_ID,
            encrypted_secret="v1:" + incomplete,
        )


def test_decrypted_secret_is_bound_to_original_source_id():
    token = encrypt_secret()

    with pytest.raises(DeviceSecretEncryptionError):
        make_cipher().decrypt(
            source_id="MT5-ANOTHER-DEVICE",
            encrypted_secret=token,
        )


def test_invalid_decrypt_source_id_is_rejected():
    token = encrypt_secret()

    with pytest.raises(DeviceSecretEncryptionError):
        make_cipher().decrypt(
            source_id="",
            encrypted_secret=token,
        )


@pytest.mark.parametrize(
    "key",
    [
        "",
        "ab" * 31,
        "ab" * 33,
        "zz" * 32,
        None,
        123,
    ],
)
def test_invalid_encryption_keys_are_rejected(key):
    with pytest.raises(DeviceSecretEncryptionError):
        DeviceSecretCipher(key)
