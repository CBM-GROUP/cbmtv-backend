"""Email lookup without choosing an arbitrary account from a legacy collision."""

from contextlib import contextmanager

from django.db import connection, transaction

from .models import User


def unique_email_user(email):
    users = list(User.objects.filter(email__iexact=email.strip()).order_by('pk')[:2])
    return users[0] if len(users) == 1 else None


@contextmanager
def email_identity_guard(email):
    """Serialize cooperating email writers on PostgreSQL, without a schema change.

    Validation is repeated inside this transaction. A future DB uniqueness
    constraint still needs an independently authorized collision remediation.
    SQLite is used for local tests; PostgreSQL is the production database.
    """
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                # Use the database's case conversion, as its iexact lookup does,
                # rather than assuming Python and database Unicode casing agree.
                cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(UPPER(%s), 0))', [email.strip()])
        yield
