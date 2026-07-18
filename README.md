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

Ollama enrichment is optional. Run Ollama on the host and expose it at `OLLAMA_BASE_URL`
before calling the semantic enrichment endpoints.
