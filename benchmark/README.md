# QueryX benchmark runner

Questo benchmark esercita il normale endpoint `POST /query/natural-language`: non replica classificazione, planning, validazione o execution. Prima dell'esecuzione legge `GET /assets` soltanto per tradurre gli ID opachi in nomi logici e backend.

Prerequisiti: QueryX e Ollama avviati, discovery MySQL/MongoDB completata e relativi asset promossi; per i casi DuckDB devono essere disponibili i dataset demo Olist e le relazioni dichiarate usate dal caso ricavi.

```bash
python benchmark/run.py \
  --base-url http://localhost:8000 \
  --cases benchmark/cases.json \
  --output-dir benchmark/results \
  --model-label qwen3.5-9b \
  --timeout 330
```

Il corpus contiene 24 casi DuckDB, MySQL, MongoDB, incertezza e riformulazioni. Ogni run produce un JSON dettagliato, un CSV e un summary JSON con accuratezze, pass rate, latenze median/p95 e breakdown per categoria.

`expected_result` è opzionale. Quando presente, il confronto è strutturale: l'ordine delle righe è ignorato per default, può essere reso significativo con `ordered_rows: true`, e `numeric_tolerance` controlla la tolleranza numerica. Il testo libero della spiegazione non viene confrontato.

Errori HTTP, timeout e casi falliti vengono registrati e il runner continua. L'exit code resta zero anche con casi benchmark falliti; un valore non zero indica soltanto un errore del runner, per esempio un file casi illeggibile o un output non scrivibile.
