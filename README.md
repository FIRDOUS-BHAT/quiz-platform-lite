# Quiz Platform Lite

`quiz-platform-lite` is the low-traffic fork of the main quiz platform.

It keeps the same institute-facing product shape:
- student login, dashboard, attempts, and results
- admin login, quiz upload, scheduling, and monitoring
- registration and payment-state audit logging
- Postgres-backed attempts and results

It removes the expensive distributed pieces from the original project:
- no Kafka / Redpanda
- no separate worker service
- no Redis dependency

Instead, submissions are scored synchronously inside the FastAPI app and results are written directly to Postgres. This is the right tradeoff for a small institute expecting roughly `100-200` concurrent students.

## Architecture

```text
Browser -> FastAPI app -> PostgreSQL
```

## Why This Fork Exists

The main repo is built like a high-scale system. That is useful if you want queue-based submission processing and independent scaling for workers, but it is unnecessary operational cost for a modest exam deployment.

This fork is intended for:
- one institute or one coaching center
- low to moderate concurrent usage
- cheap VPS deployment
- simpler backups and operations

## Recommended Hosting

Deploy this version on a single VPS:
- 2 vCPU / 4 GB RAM is a practical starting point
- Docker Compose is enough
- Caddy or Nginx can terminate TLS in front of the app

## Quick Start

1. Copy the environment file.

```bash
cp .env.example .env
```

2. Change these before production:

```env
CSRF_SECRET_KEY=replace-me
BOOTSTRAP_ADMIN_PASSWORD=replace-me
APP_TIMEZONE=Asia/Kolkata
PUBLIC_BASE_URL=https://your-domain.example
SECURE_COOKIES=true
ENVIRONMENT=production
TRUSTED_HOSTS=your-domain.example,localhost
PAYU_MERCHANT_KEY=replace-me
PAYU_MERCHANT_SALT=replace-me
```

3. Start the stack.

```bash
docker compose up -d --build
```

### Operational commands

Use these to restart, rebuild, or inspect the running services after you change code or config:

```bash
docker compose restart app     # restart only the FastAPI container
docker compose restart        # restart every service in the stack
docker compose stop app        # stop just the app without destroying volume data
docker compose stop           # stop the whole stack
docker compose down           # stop and remove containers (add -v to drop named volumes)
docker compose up -d --build  # rebuild the image before launching the app
docker compose logs -f app    # stream app logs to check readiness
```

4. Open the app.

- `http://localhost:8000/app`
- `http://localhost:8000/app/admin/login`

## Database Migrations

Alembic is now included for schema management:

```bash
alembic upgrade head
```

For older environments, the app still keeps a compatibility startup schema initializer, but Alembic should be the primary deployment path going forward.

## Payment Gateway

The register flow is now server-driven:
- the candidate submits the registration form first
- the app creates a pending PayU transaction on your server
- the browser is then redirected to PayU with a server-generated hash
- PayU returns to `/app/payments/payu/callback`, where the app verifies the callback hash before confirming payment

For production, make sure these are set:

```env
PUBLIC_BASE_URL=https://your-domain.example
PAYU_MERCHANT_KEY=your-merchant-key
PAYU_MERCHANT_SALT=your-merchant-salt
PAYU_PAYMENT_URL=https://secure.payu.in/_payment
PAYU_CERTIFICATE_FEE=100.00
PAYU_PRODUCT_INFO=Quiz Registration
```

`PUBLIC_BASE_URL` should be the public HTTPS origin that PayU can reach for success/failure callbacks.

## Tests

Run the registration and payment workflow tests with:

```bash
pytest
```

## Seeding a Quiz

You can seed a JSON quiz directly into Postgres:

```bash
python3 seed_quiz.py --file sample_quiz.json --id demo-quiz
```

Or use the admin upload flow in the web UI for Excel-based quiz imports.

## Repository Status

`quiz-platform-lite` now lives as its own standalone git repository. The old scaffolded copy under `quiz-platform/variants/quiz-platform-lite/` should be treated as deprecated and removed from the parent project.
