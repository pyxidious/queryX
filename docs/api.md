# API di QueryX

La documentazione OpenAPI interattiva è disponibile in:

```text
http://localhost:8000/docs
```

## Stato del sistema

### `GET /health`

Verifica lo stato dell'applicazione.

```bash
curl http://localhost:8000/health
```

### `GET /worker/status`

Restituisce lo stato del worker.

## Sorgenti e catalogo

### `GET /sources`

Elenca le sorgenti configurate.

```bash
curl http://localhost:8000/sources
```

### `POST /sources/{source_id}/scan`

Avvia discovery e profiling della sorgente.

```bash
curl -X POST http://localhost:8000/sources/<source-id>/scan
```

### `GET /catalog/current`

Restituisce il catalogo corrente.

```bash
curl http://localhost:8000/catalog/current
```

## Ingestion

### `POST /ingestions/uploads`

Carica un file CSV o Parquet.

```bash
curl -X POST http://localhost:8000/ingestions/uploads   -F 'file=@./orders.csv'   -F 'logical_name=orders'
```

### `GET /ingestions/{job_id}`

Restituisce lo stato del job.

### `GET /ingestions/{job_id}/preview`

Restituisce una preview limitata.

### `POST /ingestions/{job_id}/cancel`

Richiede la cancellazione del job.

## Asset

### `GET /assets`

Elenca gli asset catalogati.

### `GET /assets/{asset_id}`

Restituisce i dettagli di un asset.

## Query logiche

### `POST /query/validate`

Valida un `LogicalQueryPlan` senza eseguirlo.

```bash
curl -X POST http://localhost:8000/query/validate   -H 'Content-Type: application/json'   -d @plan.json
```

### `POST /query/execute`

Valida ed esegue il piano.

```bash
curl -X POST http://localhost:8000/query/execute   -H 'Content-Type: application/json'   -d @plan.json
```

## Linguaggio naturale

### `POST /query/natural-language`

Esempio:

```bash
curl -X POST http://localhost:8000/query/natural-language   -H 'Content-Type: application/json'   -d '{
    "question": "Quanti ordini ci sono per stato?",
    "execute": true
  }'
```

La risposta può contenere:

- `classification`;
- `reason`;
- `clarification_question`;
- `normalized_plan`;
- `output_schema`;
- `result`;
- `answer`;
- metriche temporali;
- warning.

Le richieste ambigue o non supportate non raggiungono compiler ed executor.

## Sicurezza

Il client non può fornire:

- SQL arbitrario;
- pipeline MongoDB;
- connection string;
- credenziali;
- path fisici;
- nomi interni di viste o collezioni.

Tutti i piani passano dal validatore deterministico prima dell'esecuzione.
