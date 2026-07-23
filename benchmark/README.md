# QueryX benchmark runner

Il benchmark esercita il normale endpoint `POST /query/natural-language`: non replica classificazione, retrieval, planning, validazione o execution. Legge `GET /assets` per associare gli ID opachi a nome, backend e campi catalogati. Questo consente anche controlli strutturali senza usare un secondo LLM come giudice.

## Obiettivi e corpus

Il corpus misura accuratezza funzionale, robustezza semantica, consistenza tra riformulazioni e run ripetuti, selezione del repository, validità del piano, correttezza dell'esecuzione e gestione prudente dell'incertezza. Include casi DuckDB, MySQL e MongoDB, riformulazioni, richieste ambigue o incomplete e analisi non calcolabili per dati mancanti.

Ogni caso conserva i campi storici e può aggiungere:

- `operation_type`: tipo atteso, per esempio `count`, `filter`, `aggregation`, `group_by`, `temporal_grouping`, `multi_asset` o `unsupported_analysis`;
- `difficulty`: `easy`, `medium` o `hard`;
- `uncertainty_type`: tipo di incertezza, oppure `none`;
- `equivalence_group`: collega domande semanticamente equivalenti;
- `repeat_count`: numero di richieste identiche, default `1`;
- `notes`: annotazioni sperimentali libere.

I campi nuovi sono opzionali: i file casi precedenti continuano a funzionare. `expected_result` resta facoltativo ed è usato soltanto per risultati noti e stabili. Il confronto verifica colonne e righe quando dichiarate, gestisce `null`, booleani, valori annidati semplici e numeri int/float equivalenti. L'ordine delle righe è ignorato per default o con `unordered: true`; `unordered: false` e `ordered_rows: true` richiedono l'ordine esatto. `rows_prefix` consente di verificare un prefisso ordinato insieme al `row_count`, utile per risultati top-k stabili senza duplicare nel corpus tutte le righe. `numeric_tolerance`, normalmente `1e-6`, assorbe soltanto le normali differenze floating point.

## Consistenza

Per `repeat_count > 1` il JSON dettagliato conserva ogni ripetizione, inclusi classificazione, backend, asset, validità del piano, risultato, errore, latenze e pass/fail. Il record aggregato espone consistenza di classificazione, backend, asset, validità, risultato e outcome; `full_repeat_consistency` richiede coerenza su tutte le dimensioni applicabili.

Gli `equivalence_group` confrontano classificazione, backend, asset, operazione osservata, esecuzione e risultato quando verificabile. `semantic_consistency_rate` è la quota di gruppi completamente coerenti. Il testo libero delle spiegazioni non partecipa a questi confronti.

## Metriche

Il summary mantiene pass rate, accuratezza di classificazione/backend, valid-plan rate ed execution accuracy, aggiungendo:

- breakdown per categoria, backend, operazione, difficoltà, incertezza ed equivalence group;
- `repeat_consistency_rate` e `semantic_consistency_rate`;
- `structural_hallucination_rate`, basato su asset, campi, backend e operazioni non compatibili con il catalogo;
- `forced_answer_rate` e `prudent_refusal_rate` per richieste non calcolabili;
- timeout rate, error rate e result-verified rate;
- numero di casi/esecuzioni con ground truth e result accuracy complessiva, per backend e per operation type;
- mediana e p95 separate per planning, execution, explanation e totale.

Le latenze misurano tempo osservato end-to-end nelle rispettive fasi: sono **latenza e proxy osservabile del costo computazionale**, non una misura diretta del costo computazionale. Carico del sistema, cold start e rete possono influenzarle.

Il runner registra `explanation_present` e `explanation_ms`. Per risposte ambigue o non calcolabili registra anche la presenza strutturale di chiarimento o motivazione. Non valuta automaticamente qualità, correttezza o stile della spiegazione e non applica euristiche lessicali fragili.

## Ground truth dei risultati

L'accuratezza strutturale misura classificazione, backend, asset e validità del piano. L'accuratezza del risultato confronta invece le righe restituite con valori calcolati direttamente dalle sorgenti demo. Il corpus corrente contiene 20 casi con `expected_result`: 6 DuckDB, 7 MySQL e 7 MongoDB.

Sono stati scelti count, group by, sum, avg e risultati ordinati o aggregati di dimensione controllata. Sono esclusi casi ambigui/non calcolabili, raggruppamenti temporali ancora instabili, projection molto grandi o senza ordine, output troncati e documenti il cui timestamp init dipende dall'istante di creazione del volume. `result_verified_rate` è la quota di casi logici dotati di ground truth; `result_accuracy` usa esclusivamente le relative esecuzioni come denominatore.

La ground truth è rigenerabile con query statiche e read-only, senza passare dal planner o dall'endpoint QueryX. Il percorso ufficiale usa il container applicativo, che dispone già degli hostname Docker, delle credenziali configurate e del volume DuckDB:

```bash
docker compose exec queryx python -m benchmark.generate_ground_truth
```

`benchmark` è incluso nell'immagine e montato in `/app/benchmark`, quindi l'aggiornamento di `cases.json` e gli artefatti sotto `results/` persistono direttamente nel checkout host. Lo script individua `cases.json` relativamente al package, aggiorna esclusivamente gli `expected_result` già predisposti e non modifica alcun dato sorgente. Usa `MYSQL_URL`, `MONGODB_URL` e `DUCKDB_PATH` della configurazione QueryX, apre DuckDB in modalità read-only e non stampa credenziali.

Path alternativi possono essere indicati senza dipendere dalla working directory:

```bash
docker compose exec queryx python -m benchmark.generate_ground_truth \
  --cases /app/benchmark/cases.json \
  --output /app/benchmark/ground-truth.json
```

Prima del calcolo vengono verificati file, connessioni, tabelle, collection e permessi. Gli errori operativi sono sintetici; `--debug` abilita il traceback per diagnosi. I valori mantengono la precisione restituita dalle sorgenti e usano la tolleranza numerica documentata, senza arrotondamenti arbitrari.

## Esecuzione

Prerequisiti: QueryX e Ollama avviati, discovery MySQL/MongoDB completata, asset promossi e dataset/relazioni DuckDB richiesti dal corpus disponibili.

```bash
docker compose exec queryx python -m benchmark.run \
  --base-url http://127.0.0.1:8000 \
  --cases /app/benchmark/cases.json \
  --output-dir /app/benchmark/results \
  --model-label qwen3.5-9b-100k
```

Ogni run produce:

- JSON dettagliato con un record logico per caso e l'array delle ripetizioni;
- CSV con una riga per singola ripetizione;
- summary JSON con casi logici, richieste effettive, metriche e breakdown.

Errori HTTP, timeout e fallimenti dei casi vengono registrati e il runner prosegue. L'exit code è diverso da zero soltanto se fallisce il runner stesso.

Per svolgere più run manuali, ripetere il comando cambiando `--model-label` o le condizioni sperimentali. Il parametro identifica gli artefatti ma non seleziona né configura automaticamente il modello. In futuro, risultati con label diverse potranno essere confrontati esternamente mantenendo invariati corpus, seed, configurazione e stato dei repository; l'esecuzione automatica multi-modello non è ancora implementata.

Gli stessi passaggi sono disponibili come `make up`, `make seed`, `make ground-truth`, `make benchmark` e `make reproduce`. `MODEL_LABEL=... make benchmark` cambia soltanto l'etichetta degli artefatti.
