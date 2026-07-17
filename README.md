# QueryX

Skeleton FastAPI service for scanning MySQL and MongoDB metadata into an internal SQLite catalog.

## Start

```bash
cp .env.example .env
docker compose up --build
```

## Endpoints

- `GET /health`
- `POST /catalog/scan`
- `GET /catalog/latest`

Ollama is configurable but not called in this phase.
