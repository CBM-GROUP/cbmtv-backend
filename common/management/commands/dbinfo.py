"""
Answer "which database am I actually talking to, and is it up to date?"

settings.py resolves the database in three steps — DATABASE_URL, then DB_* vars,
then a silent fallback to a local sqlite file. The fallback is the useful
default for a fresh checkout and the trap for everyone else: with no DB_* vars
set, `manage.py migrate` cheerfully writes to db.sqlite3 while you believe you
are migrating Postgres, and nothing appears to "reflect".

Run `python manage.py dbinfo` to see the truth, in Docker or out.
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Show the resolved database connection and any unapplied migrations."

    def handle(self, *args, **options):
        settings_dict = connection.settings_dict
        engine = settings_dict["ENGINE"].rsplit(".", 1)[-1]
        is_sqlite = engine == "sqlite3"

        self.stdout.write(self.style.MIGRATE_HEADING("Database"))
        self.stdout.write(f"  engine   {engine}")
        self.stdout.write(f"  name     {settings_dict['NAME']}")
        if not is_sqlite:
            self.stdout.write(f"  host     {settings_dict['HOST']}:{settings_dict['PORT']}")
            self.stdout.write(f"  user     {settings_dict['USER']}")

        if is_sqlite:
            self.stdout.write(
                self.style.WARNING(
                    "  source   sqlite fallback -- no DATABASE_URL and no DB_NAME in the\n"
                    "           environment. Migrations run here will NOT touch Postgres.\n"
                    "           For the Docker stack use: docker compose exec api python manage.py ..."
                )
            )

        try:
            connection.ensure_connection()
        except Exception as exc:  # noqa: BLE001 - surfacing the driver's own message is the point
            self.stdout.write(self.style.ERROR(f"  status   UNREACHABLE: {exc}"))
            return

        self.stdout.write(self.style.SUCCESS("  status   connected"))

        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Migrations"))
        self.stdout.write(f"  applied    {len(executor.loader.applied_migrations)}")

        if not plan:
            self.stdout.write(self.style.SUCCESS("  unapplied  0 -- schema is up to date"))
            return

        self.stdout.write(self.style.ERROR(f"  unapplied  {len(plan)} -- run `manage.py migrate`"))
        for migration, _backwards in plan:
            self.stdout.write(self.style.ERROR(f"             {migration.app_label}.{migration.name}"))
