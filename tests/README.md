# Test QueryX

Questa directory contiene la suite automatica del progetto.

## Esecuzione rapida

Con Docker:

```bash
make test
```

oppure:

```bash
docker compose exec queryx pytest -q
```

In ambiente locale:

```bash
pytest -q
```

## Verifiche aggiuntive

```bash
python -m compileall -q queryx tests
docker compose config --quiet
git diff --check
```

## Obiettivi della suite

I test verificano principalmente:

- modelli e schema del `LogicalQueryPlan`;
- validazione di asset, campi, alias e operatori;
- compilazione per DuckDB, MySQL e MongoDB;
- executor e gestione degli errori;
- ingestion e processing;
- catalogo e relazioni;
- classificazione e pianificazione in linguaggio naturale;
- sicurezza e limiti delle query;
- comportamento del worker.

## Modalità offline

La suite è progettata per essere eseguita senza dipendere da servizi esterni non controllati. Quando necessario, i componenti LLM o i backend vengono simulati o configurati tramite fixture.

## Debug

Per mostrare più dettagli:

```bash
pytest -vv
```

Per eseguire un singolo file:

```bash
pytest -q tests/<nome_file>.py
```

Per eseguire un singolo test:

```bash
pytest -q tests/<nome_file>.py::<nome_test>
```
