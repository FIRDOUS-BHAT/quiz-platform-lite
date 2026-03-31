# Quiz Platform Lite

`quiz-platform-lite` is the low-traffic fork of the main quiz platform.

It keeps the same institute-facing product shape:
- student login, dashboard, attempts, and results
- admin login, quiz upload, scheduling, and monitoring
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
SECURE_COOKIES=true
ENVIRONMENT=production
```

3. Start the stack.

```bash
docker compose up -d --build
```

4. Open the app.

- `http://localhost:8000/app`
- `http://localhost:8000/app/admin/login`

## Seeding a Quiz

You can seed a JSON quiz directly into Postgres:

```bash
python3 seed_quiz.py --file sample_quiz.json --id demo-quiz
```

Or use the admin upload flow in the web UI for Excel-based quiz imports.

## Repository Status

`quiz-platform-lite` now lives as its own standalone git repository. The old scaffolded copy under `quiz-platform/variants/quiz-platform-lite/` should be treated as deprecated and removed from the parent project.
