# Benchmark QueryX

Questa directory contiene i casi di test, gli strumenti di esecuzione e i risultati del benchmark utilizzato per confrontare diversi modelli linguistici all'interno della pipeline QueryX.

## Prerequisiti

Prima di eseguire il benchmark:

```bash
docker compose up --build -d
docker compose exec queryx python -m queryx.tools.seed_demo
```

Assicurarsi inoltre che Ollama sia avviato e che il modello configurato in `.env` sia disponibile.

## Generazione della ground truth

```bash
docker compose exec queryx python -m benchmark.generate_ground_truth
```

Questo comando aggiorna i risultati attesi nei casi di benchmark sulla base dello stato corrente dei dati.

## Esecuzione

```bash
docker compose exec queryx python -m benchmark.run   --base-url http://127.0.0.1:8000   --cases /app/benchmark/cases.json   --output-dir /app/benchmark/results   --model-label qwen3.5-9b-100k
```

In alternativa:

```bash
MODEL_LABEL=qwen3.5-9b-100k make benchmark
```

## Output

I risultati vengono salvati in:

```text
benchmark/results/
```

Il runner produce file JSON, CSV e riepiloghi aggregati utili per confrontare:

- classificazione;
- validità del piano;
- selezione del backend;
- successo di esecuzione;
- correttezza del risultato;
- ripetibilità;
- consistenza semantica;
- prudenza;
- errori;
- latenza.

## Confronto tra modelli

Per confrontare più modelli:

1. aggiornare il modello in `.env`;
2. riavviare il servizio applicativo;
3. eseguire nuovamente il benchmark con una nuova `model-label`;
4. conservare i risultati in `benchmark/results/`.

Usare la stessa configurazione, gli stessi dati e gli stessi casi per mantenere il confronto corretto.
