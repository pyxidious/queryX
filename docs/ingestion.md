# Ingestion e preparazione dei dati

QueryX utilizza procedure differenti per file locali, MySQL e MongoDB.

## CSV e Parquet

Il flusso per i file caricati manualmente è:

```text
upload
→ staging
→ inspection
→ creazione o aggiornamento dell'asset
→ registrazione della versione
→ normalizzazione Parquet
→ creazione della vista DuckDB
```

Esempio:

```bash
curl -X POST http://localhost:8000/ingestions/uploads   -F 'file=@./orders.csv'   -F 'logical_name=orders'
```

Sono supportati solo file CSV e Parquet singoli.

Non sono supportati:

- ZIP;
- URL remoti;
- download automatici;
- upload multipli;
- integrazione diretta con Kaggle.

## Seed MySQL e MongoDB

I dati dimostrativi vengono creati con:

```bash
docker compose exec queryx python -m queryx.tools.seed_demo
```

Valori predefiniti:

| Backend | Dataset | Totale |
|---|---|---:|
| MySQL | customers | 10.000 |
| MySQL | orders | 100.000 |
| MongoDB | profiles | 10.000 |
| MongoDB | events | 100.000 |

Il seed è deterministico e utilizza identificativi stabili.

## Discovery e profiling

Dopo il caricamento o il seed, le sorgenti vengono analizzate per registrare nel catalogo:

- asset;
- campi;
- tipi;
- relazioni;
- strutture annidate;
- array MongoDB;
- binding fisici.

La scansione di una sorgente può essere avviata tramite:

```bash
curl -X POST http://localhost:8000/sources/<source-id>/scan
```

Le sorgenti disponibili possono essere elencate con:

```bash
curl http://localhost:8000/sources
```

## Idempotenza

Il sistema distingue tra contenuto, versione e asset logico.

In generale:

- lo stesso contenuto può riutilizzare una versione già pronta;
- un contenuto differente produce una nuova versione;
- lo stesso file può appartenere ad asset differenti;
- il seed può essere rilanciato senza produrre duplicati.

## Controlli

Al termine della preparazione vengono verificati:

- raggiungibilità del backend;
- presenza degli asset;
- schema osservato;
- cardinalità attese;
- stato dei binding;
- disponibilità della vista DuckDB.

Questi controlli impediscono che un errore di caricamento venga confuso con un errore del modello linguistico.
