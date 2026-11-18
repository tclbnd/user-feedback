"""Request handlers of the account service.

Each handler borrows one connection for the duration of the request.
"""
from .pool import Borrowed


class HandlerError(Exception):
    pass


def get_profile(pool, user):
    with Borrowed(pool) as conn:
        return conn.run_prepared("get_profile", f"profile:{user}")


def update_profile(pool, user, value, fail=False):
    with Borrowed(pool) as conn:
        conn.begin()
        conn.write(f"profile:{user}", value)
        if fail:
            raise HandlerError("validation failed after write")
        conn.commit()


def report_time(pool, tenant_tz=None):
    """Render the server time for a tenant; tenants may pick their timezone."""
    with Borrowed(pool) as conn:
        if tenant_tz:
            conn.set("timezone", tenant_tz)
        return conn.now()


def tenant_read(pool, schema, key):
    """Multi-schema tenants switch the search path for their request."""
    with Borrowed(pool) as conn:
        conn.set("search_path", schema)
        return conn.read(key)


def plain_read(pool, key):
    with Borrowed(pool) as conn:
        return conn.read(key)


def audit_write(pool, key, value):
    with Borrowed(pool) as conn:
        conn.begin()
        conn.set("statement_timeout", 5000, local=True)
        conn.write(key, value)
        conn.commit()
