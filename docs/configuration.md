# Configurazione di QueryX

La configurazione principale è definita in `.env.example`.

Per iniziare:

```bash
cp .env.example .env
```

## Aree principali

### MySQL

Variabili con prefisso:

```text
MYSQL_*
```

Configurano host, porta, database, credenziali e timeout.

### MongoDB

Variabili con prefisso:

```text
MONGODB_*
```

Configurano URI, database e timeout.

### Ollama

Variabili con prefisso:

```text
OLLAMA_*
```

Definiscono endpoint, modello, timeout, context window e parametri di generazione.

Assicurarsi che Ollama sia avviato:

```bash
ollama serve
```

e che il modello sia installato:

```bash
ollama pull <modello>
```

### Catalogo e storage

Variabili principali:

```text
CATALOG_DB_PATH
DATA_RAW_DIR
DATA_STAGING_DIR
DATA_NORMALIZED_DIR
```

### DuckDB e Parquet

Variabili con prefisso:

```text
DUCKDB_*
PARQUET_*
```

Definiscono database locale, viste e parametri di normalizzazione.

### Query

Variabili principali:

```text
QUERY_DEFAULT_LIMIT
QUERY_MAX_LIMIT
QUERY_TIMEOUT_SECONDS
MYSQL_QUERY_TIMEOUT_SECONDS
MONGODB_QUERY_TIMEOUT_SECONDS
```

Le query sono sempre bounded e soggette a timeout.

### Worker

Variabili principali:

```text
QUERYX_EXECUTION_MODE
WORKER_*
```

`QUERYX_EXECUTION_MODE` può essere usato per distinguere esecuzione inline e worker separato.

## Docker Compose

Il percorso consigliato è:

```bash
docker compose up --build -d
```

Compose avvia applicazione, worker, MySQL e MongoDB.

Verifica della configurazione:

```bash
docker compose config --quiet
```

## Configurazione per benchmark

Per confronti corretti tra modelli, mantenere costanti:

- dati;
- catalogo;
- casi di test;
- temperatura;
- context window;
- timeout;
- versione del codice;
- hardware.

Cambiare soltanto il modello e la relativa etichetta del run.
