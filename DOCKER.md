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
docker compose exec api python manage.py createsuperuser
docker compose logs -f api
docker compose down
```

Add `--volumes` to `docker compose down` only when you intentionally want to
delete the local PostgreSQL data.

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
