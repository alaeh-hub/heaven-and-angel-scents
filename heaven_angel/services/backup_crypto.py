"""Encryption for the emailed backup.

The backup holds password hashes and customer data, so it is never emailed readable. It is
sealed into one opaque file (.enc): AES-256-GCM, with the key derived from the passphrase
(BACKUP_ZIP_PASSWORD) by scrypt. It is deliberately NOT a password-protected zip: mail
providers such as Gmail scan attachments, treat a locked archive they cannot look inside as
a risk, and refuse the message ("blocked because its content presents a potential security
issue"). An opaque data file is not an archive, so it goes through.

File layout:  b"HABK1\\n" | salt (16) | nonce (12) | AES-GCM ciphertext and tag.
The magic bytes are authenticated too, so a changed header or a damaged file fails to open
instead of producing garbage. The Restore screen opens this file as it is, and
scripts/decrypt_backup.py opens it by hand.
"""
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"HABK1\n"
_SALT_LEN = 16
_NONCE_LEN = 12
_HEADER_LEN = len(MAGIC) + _SALT_LEN + _NONCE_LEN
_SCRYPT = {"length": 32, "n": 2 ** 15, "r": 8, "p": 1}


class BackupDecryptError(Exception):
    """The file is not an encrypted backup, is damaged, or the passphrase is wrong."""


def _key(password, salt):
    return Scrypt(salt=salt, **_SCRYPT).derive(password.encode("utf-8"))


def is_encrypted_backup(head):
    """True if these first bytes are the start of an encrypted backup file."""
    return bytes(head[:len(MAGIC)]) == MAGIC


def encrypt_backup(data, password):
    """The encrypted file's bytes for `data` (the backup zip)."""
    salt, nonce = os.urandom(_SALT_LEN), os.urandom(_NONCE_LEN)
    return MAGIC + salt + nonce + AESGCM(_key(password, salt)).encrypt(nonce, data, MAGIC)


def decrypt_backup(blob, password):
    """The backup zip's bytes. Raises BackupDecryptError for a wrong passphrase or damaged file."""
    if len(blob) < _HEADER_LEN + 16 or not is_encrypted_backup(blob):
        raise BackupDecryptError("That is not an encrypted backup file.")
    salt = blob[len(MAGIC):len(MAGIC) + _SALT_LEN]
    nonce = blob[len(MAGIC) + _SALT_LEN:_HEADER_LEN]
    try:
        return AESGCM(_key(password, salt)).decrypt(nonce, blob[_HEADER_LEN:], MAGIC)
    except InvalidTag:
        raise BackupDecryptError("The passphrase does not open this backup, or the file is damaged.") from None
