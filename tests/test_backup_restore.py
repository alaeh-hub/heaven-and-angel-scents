"""Backup and restore carry the per-scent oil tables, and a backup made before they
existed does not leave today's oil stock beside older data."""
from decimal import Decimal

from factories import unique_suffix
from heaven_angel.services.backup import (
    build_backup_zip, empty_tables_missing_from_backup, prepare_restore, run_restore, split_sql,
)


def _rows(sql, query, params=()):
    cur = sql.cursor(dictionary=True)
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def _scent_oil(sql, stock="250"):
    code = "ZZ" + unique_suffix().upper()
    cur = sql.cursor()
    cur.execute("INSERT INTO scent_oils (base_code, scent_name, cost_per_unit, stock_qty) VALUES (%s, 'Backup Oil', 3.8, %s)",
                (code, stock))
    cur.execute("INSERT INTO scent_oil_restocks (base_code, qty, cost, purchased_at) VALUES (%s, %s, 950, '2026-11-04')",
                (code, stock))
    sql.commit()
    cur.close()
    return code


def _write_backup(app, path):
    with app.app_context():
        data, _name, summary = build_backup_zip(2 ** 40)
    path.write_bytes(data)
    return summary


def test_the_backup_includes_the_scent_oil_tables(app, sql, tmp_path):
    code = _scent_oil(sql)
    backup = tmp_path / "backup.zip"
    summary = _write_backup(app, backup)
    assert summary["tables"] >= 2
    with app.app_context():
        statements, tables, zf = prepare_restore(str(backup))
        zf.close()
    assert "scent_oils" in tables and "scent_oil_restocks" in tables
    assert any(code in s for s in statements if s.startswith("INSERT INTO `scent_oils`"))


def test_a_restore_brings_back_each_scents_oil(app, sql, tmp_path):
    code = _scent_oil(sql, "250")
    backup = tmp_path / "backup.zip"
    _write_backup(app, backup)

    cur = sql.cursor()
    cur.execute("UPDATE scent_oils SET stock_qty = 0 WHERE base_code = %s", (code,))
    cur.execute("DELETE FROM scent_oil_restocks WHERE base_code = %s", (code,))
    sql.commit()
    cur.close()

    with app.app_context():
        statements, tables, zf = prepare_restore(str(backup))
        zf.close()
        run_restore(statements)
        assert empty_tables_missing_from_backup(tables) == []        # in the backup, so kept
    assert _rows(sql, "SELECT stock_qty FROM scent_oils WHERE base_code = %s", (code,))[0]["stock_qty"] == Decimal("250")
    assert len(_rows(sql, "SELECT 1 FROM scent_oil_restocks WHERE base_code = %s", (code,))) == 1


def test_a_backup_from_before_scent_oils_empties_them(app, sql, tmp_path):
    _scent_oil(sql)
    full = tmp_path / "full.zip"
    _write_backup(app, full)
    with app.app_context():
        statements, _tables, zf = prepare_restore(str(full))
        zf.close()
    older = [s for s in statements if "`scent_oil" not in s]          # a backup taken before those tables existed
    old_backup = tmp_path / "older.sql"
    old_backup.write_text("-- Heaven & Angel Scents database backup\n" + ";\n".join(older) + ";\n", encoding="utf-8")
    assert len(split_sql(old_backup.read_text(encoding="utf-8"))) == len(older)

    with app.app_context():
        statements, tables, _zf = prepare_restore(str(old_backup))
        run_restore(statements)
        assert empty_tables_missing_from_backup(tables) == ["scent_oil_restocks", "scent_oils"]
    assert _rows(sql, "SELECT COUNT(*) AS n FROM scent_oils")[0]["n"] == 0
    assert _rows(sql, "SELECT COUNT(*) AS n FROM scent_oil_restocks")[0]["n"] == 0
