"""The emailed backup is one opaque AES-256 file (.enc), not a locked zip (Gmail refuses an
archive it cannot look inside). Restore opens it as it is, with BACKUP_ZIP_PASSWORD."""
import pytest

from heaven_angel.services import backup as bk
from heaven_angel.services.backup_crypto import (
    MAGIC, BackupDecryptError, decrypt_backup, encrypt_backup, is_encrypted_backup,
)

PASSWORD = "correct horse battery staple"
SQL = bk._HEADER + "\nSELECT 1;\n"


def _inner_zip():
    return bk._zip([("MANIFEST.txt", b"manifest"), ("database.sql", SQL.encode("utf-8"))])


def _enc_file(tmp_path, password=PASSWORD):
    """What mailer.send_backup_email attaches."""
    path = tmp_path / "heaven-angel-backup.enc"
    path.write_bytes(encrypt_backup(_inner_zip(), password))
    return str(path)


# ---------------------------------------------------------------- the encryption itself (no database)

def test_encrypt_then_decrypt_round_trips():
    data = _inner_zip()
    blob = encrypt_backup(data, PASSWORD)
    assert is_encrypted_backup(blob) and blob.startswith(MAGIC)
    assert data not in blob                                  # nothing readable inside
    assert decrypt_backup(blob, PASSWORD) == data


def test_the_same_data_encrypts_differently_each_time():
    # a fresh salt and nonce every time, so two backups never share a key stream
    assert encrypt_backup(b"same", PASSWORD) != encrypt_backup(b"same", PASSWORD)


def test_a_wrong_passphrase_or_a_damaged_file_does_not_open():
    blob = encrypt_backup(_inner_zip(), PASSWORD)
    with pytest.raises(BackupDecryptError):
        decrypt_backup(blob, "not the passphrase")
    flipped = bytearray(blob)
    flipped[-5] ^= 0x01                                      # one bit changed in the data
    with pytest.raises(BackupDecryptError):
        decrypt_backup(bytes(flipped), PASSWORD)
    with pytest.raises(BackupDecryptError):
        decrypt_backup(b"not a backup at all", PASSWORD)


def test_the_attachment_is_not_a_zip_archive():
    # the point of the format: nothing for a mail scanner to treat as a locked archive
    blob = encrypt_backup(_inner_zip(), PASSWORD)
    assert not blob.startswith(b"PK")


# ---------------------------------------------------------------- restore reads it

def test_the_encrypted_email_attachment_restores_as_it_is(app, tmp_path):
    with app.app_context():
        app.config["BACKUP_ZIP_PASSWORD"] = PASSWORD
        sql, zf = bk.read_backup_sql(_enc_file(tmp_path))
        try:
            assert sql == SQL
            assert "MANIFEST.txt" in zf.namelist()
        finally:
            zf.close()


def test_a_plain_backup_zip_still_restores(app, tmp_path):
    plain = tmp_path / "plain.zip"
    plain.write_bytes(_inner_zip())
    with app.app_context():
        sql, zf = bk.read_backup_sql(str(plain))
        zf.close()
    assert sql == SQL


def test_an_encrypted_backup_needs_the_right_password(app, tmp_path):
    path = _enc_file(tmp_path)
    with app.app_context():
        app.config["BACKUP_ZIP_PASSWORD"] = ""
        with pytest.raises(bk.RestoreError, match="encrypted"):
            bk.read_backup_sql(path)
        app.config["BACKUP_ZIP_PASSWORD"] = "not the password"
        with pytest.raises(bk.RestoreError, match="does not open"):
            bk.read_backup_sql(path)


# ---------------------------------------------------------------- the rest of backup and restore

def test_a_restore_of_an_older_backup_gets_the_newer_columns_back(app, sql):
    """A backup older than the chemist/labor feature restores tables without those columns, and the
    once-per-process column check has already run: reapply_data_fixes() must add them again."""
    from heaven_angel.routes.admin.batch_formulas import reapply_data_fixes
    cur = sql.cursor()
    cur.execute("ALTER TABLE bulk_batches DROP COLUMN chemist_cost, DROP COLUMN labor_cost")
    cur.execute("ALTER TABLE bulk_formula_settings DROP COLUMN chemist_per_bottle, DROP COLUMN labor_per_bottle")
    sql.commit()
    with app.app_context():
        reapply_data_fixes()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = DATABASE() "
                "AND table_name IN ('bulk_batches', 'bulk_formula_settings') "
                "AND column_name IN ('chemist_cost', 'labor_cost', 'chemist_per_bottle', 'labor_per_bottle')")
    found = {r[0] for r in cur.fetchall()}
    cur.close()
    assert found == {"chemist_cost", "labor_cost", "chemist_per_bottle", "labor_per_bottle"}


def test_no_backup_is_built_when_there_is_no_password(app, sql, monkeypatch):
    """Without BACKUP_ZIP_PASSWORD nothing can be sent, so the job stops before building the zip and
    says why in the Admin Log."""
    from heaven_angel.services import backup_jobs
    monkeypatch.setitem(app.config, "BACKUP_ZIP_PASSWORD", "")
    monkeypatch.setattr(backup_jobs, "build_backup_zip",
                        lambda *_a, **_k: pytest.fail("the zip must not be built without a password"))
    assert backup_jobs.backup_lock.acquire(blocking=False)
    backup_jobs.run_backup_job(app, None, "tester")          # releases the lock itself
    cur = sql.cursor()
    cur.execute("SELECT details FROM admin_actions WHERE action = 'BACKUP_EMAIL_FAILED' ORDER BY action_id DESC LIMIT 1")
    row = cur.fetchone()
    cur.close()
    assert row and "BACKUP_ZIP_PASSWORD" in row[0]


def test_the_email_backup_button_explains_a_missing_password(app, client, sql, monkeypatch):
    from factories import login, make_user
    monkeypatch.setitem(app.config, "MAIL_SERVER", "smtp.example.test")
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", "from@example.test")
    monkeypatch.setitem(app.config, "BACKUP_NOTIFY_EMAIL", "to@example.test")
    monkeypatch.setitem(app.config, "BACKUP_ZIP_PASSWORD", "")
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    resp = client.post("/admin/backup/email")
    assert resp.status_code == 400
    assert "BACKUP_ZIP_PASSWORD" in resp.get_json()["error"]


# ---------------------------------------------------------------- what the backup log says

def test_a_small_backup_is_shown_in_kb_not_as_zero_mb():
    from heaven_angel.services.mailer import mask_email, size_text
    assert size_text(41_000) == "40 KB" and size_text(900) == "1 KB" and size_text(2_600_000) == "2.5 MB"
    assert mask_email("owner@gmail.com") == "o****@gmail.com" and mask_email("") == ""


def test_a_refused_recipient_is_not_reported_as_sent(app, monkeypatch):
    import smtplib

    from heaven_angel.services import mailer

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, msg): return {"owner@example.test": (550, b"no such user")}

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setitem(app.config, "MAIL_SERVER", "smtp.example.test")
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", "from@example.test")
    monkeypatch.setitem(app.config, "BACKUP_NOTIFY_EMAIL", "owner@example.test")
    monkeypatch.setitem(app.config, "BACKUP_ZIP_PASSWORD", PASSWORD)
    with app.app_context():
        ok, err = mailer.send_backup_email(zip_bytes=b"PK", filename="b.zip", subject="s", body="b")
    assert not ok and "refused the recipient" in err
