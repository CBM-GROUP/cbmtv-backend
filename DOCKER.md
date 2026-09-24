# CBMTV container deployment

## Run locally

Docker Compose starts the Django API and PostgreSQL:

```sh
docker compose up --build
```

The API is available at `http://localhost:8001` and its readiness endpoint is
`http://localhost:8001/health/`. PostgreSQL is exposed on local port `5433`.

Copy `.env.example` to `.env` to override the safe local defaults. Do not use
those defaults in production.

Useful commands:

```sh
docker compose exec api python manage.py seed_superadmin
docker compose exec api python manage.py createsuperuser
docker compose logs -f api
docker compose down
```

Add `--volumes` to `docker compose down` only when you intentionally want to
delete the local PostgreSQL data.

## Which database am I on?

`settings.py` resolves the database in three steps: `DATABASE_URL`, then `DB_*`
vars, then a **silent fallback to `db.sqlite3`**. That fallback is why
migrations can look applied and still not "reflect" — the host shell and the
container are two different databases, and both report themselves up to date.

```bash
docker compose exec api python manage.py dbinfo   # -> postgresql @ postgres:5432
python manage.py dbinfo                           # -> sqlite3 fallback, with a warning
```

Always run migrations where the database is:

```bash
docker compose exec api python manage.py migrate
docker compose exec api python manage.py seed_superadmin
```

To point a host-side `runserver` at the container's Postgres instead of sqlite,
set the `DB_*` vars (compose publishes Postgres on 5433 to avoid colliding with
a local install):

```bash
DB_NAME=cbmtv DB_USER=cbmtv DB_PASSWORD=cbmtv_local_password DB_HOST=127.0.0.1 DB_PORT=5433 python manage.py runserver 8001
```

**This stack is not production.** Production is Railway, with its own managed
Postgres reached via `DATABASE_URL`. Migrating locally proves the migrations
apply cleanly; it does not change production. Production is migrated by the
`preDeployCommand` in `railway.json` on each deploy, or manually with
`railway run python manage.py migrate --noinput`.

## AWS deployment baseline

The image is suitable for Amazon ECR and an ECS/Fargate service. Build it with
`docker build -t cbmtv-api .`, then:

1. Build and push the image to a private ECR repository.
2. Create an RDS PostgreSQL database in private subnets.
3. Put `SECRET_KEY` and the RDS `DATABASE_URL` in AWS Secrets Manager.
4. Configure the ECS task with those secrets and the other values from
   `.env.example`. Set `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and
   `CORS_ALLOWED_ORIGINS` to the real domains. After HTTPS is confirmed, enable
   `SECURE_SSL_REDIRECT` and introduce HSTS deliberately.
5. Expose container port `8000` through an Application Load Balancer. Configure
   its target-group health check path as `/health/`.
6. Run migrations as a one-off ECS task during deployment. For a multi-task
   service, set `RUN_MIGRATIONS=false` on the long-running web tasks to prevent
   concurrent migration attempts.

Static files are served by WhiteNoise. If file uploads are added later, use
S3-backed Django storage because Fargate task filesystems are ephemeral.
