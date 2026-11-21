"""Decrypt an emailed backup (.enc) back to its zip, for inspection or mysql restore.

    python scripts/decrypt_backup.py heaven-angel-backup-20261119-1430.enc
    python scripts/decrypt_backup.py backup.enc restored.zip

The passphrase (BACKUP_ZIP_PASSWORD) is read from a hidden prompt or that env
variable. The output file is never overwritten. The app's Restore opens .enc itself.
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from heaven_angel.services.backup_crypto import BackupDecryptError, decrypt_backup, is_encrypted_backup  # noqa: E402


def main(argv):
    if not 1 <= len(argv) <= 2:
        sys.exit(__doc__)
    source = argv[0]
    target = argv[1] if len(argv) == 2 else (source[:-4] if source.lower().endswith(".enc") else source) + ".zip"
    if os.path.exists(target):
        sys.exit(f"{target} already exists; choose another name.")
    with open(source, "rb") as fh:
        blob = fh.read()
    if not is_encrypted_backup(blob):
        sys.exit("That is not an encrypted backup file from this app.")
    password = os.environ.get("BACKUP_ZIP_PASSWORD") or getpass.getpass("Backup passphrase (BACKUP_ZIP_PASSWORD): ")
    try:
        data = decrypt_backup(blob, password)
    except BackupDecryptError as err:
        sys.exit(str(err))
    with open(target, "xb") as out:
        out.write(data)
    print(f"Wrote {target} ({len(data) / 1024 / 1024:.1f} MB). Upload it to Restore, or open it like any zip.")


if __name__ == "__main__":
    main(sys.argv[1:])
