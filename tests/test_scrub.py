"""scrub_secrets must keep DB credentials out of error messages."""

from amnesic.config import ConnectionConfig
from amnesic.drivers import scrub_secrets


def _conn(pw="s3cr3t!pw", user="admin"):
    return ConnectionConfig(name="t", driver="postgres", server="h", port=5432,
                            database="d", user=user, password=pw)


def test_literal_password_masked():
    msg = "connection failed for user admin: password 's3cr3t!pw' rejected"
    out = scrub_secrets(msg, _conn())
    assert "s3cr3t!pw" not in out
    assert "***" in out


def test_password_in_connection_url_masked():
    msg = "could not connect to postgresql+psycopg2://admin:s3cr3t!pw@h:5432/d"
    out = scrub_secrets(msg, _conn())
    assert "s3cr3t!pw" not in out
    assert "admin:***@h" in out  # user kept, password masked


def test_url_encoded_password_masked():
    # password with special chars gets URL-encoded in the DSN
    conn = _conn(pw="p@ss/w0rd")
    from urllib.parse import quote_plus
    msg = f"bad dsn: mysql+pymysql://admin:{quote_plus('p@ss/w0rd')}@h/d"
    out = scrub_secrets(msg, conn)
    assert quote_plus("p@ss/w0rd") not in out
    assert "p@ss/w0rd" not in out


def test_generic_url_creds_masked_even_if_password_unknown():
    # even a credential we didn't pass gets masked by the URL regex
    conn = ConnectionConfig(name="t", driver="sqlite", database="/x.db")  # no pw
    msg = "nested error: mssql+pymssql://sa:Hunter2@host:1433/db timed out"
    out = scrub_secrets(msg, conn)
    assert "Hunter2" not in out
    assert "sa:***@host" in out


def test_sqlite_no_password_is_noop_safe():
    conn = ConnectionConfig(name="t", driver="sqlite", database="/x.db")
    msg = "no such table: orders"
    assert scrub_secrets(msg, conn) == msg
