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

I campi nuovi sono opzionali: i file casi precedenti continuano a funzionare. `expected_result` resta facoltativo ed è usato soltanto per risultati noti e stabili. Il confronto è strutturale, ignora normalmente l'ordine delle righe, supporta `ordered_rows: true` e usa `numeric_tolerance` per i numeri.

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
- mediana e p95 separate per planning, execution, explanation e totale.

Le latenze misurano tempo osservato end-to-end nelle rispettive fasi: sono **latenza e proxy osservabile del costo computazionale**, non una misura diretta del costo computazionale. Carico del sistema, cold start e rete possono influenzarle.

Il runner registra `explanation_present` e `explanation_ms`. Per risposte ambigue o non calcolabili registra anche la presenza strutturale di chiarimento o motivazione. Non valuta automaticamente qualità, correttezza o stile della spiegazione e non applica euristiche lessicali fragili.

## Esecuzione

Prerequisiti: QueryX e Ollama avviati, discovery MySQL/MongoDB completata, asset promossi e dataset/relazioni DuckDB richiesti dal corpus disponibili.

```bash
python benchmark/run.py \
  --base-url http://127.0.0.1:8000 \
  --cases benchmark/cases.json \
  --output-dir benchmark/results \
  --model-label qwen3.5-9b
```

Ogni run produce:

- JSON dettagliato con un record logico per caso e l'array delle ripetizioni;
- CSV con una riga per singola ripetizione;
- summary JSON con casi logici, richieste effettive, metriche e breakdown.

Errori HTTP, timeout e fallimenti dei casi vengono registrati e il runner prosegue. L'exit code è diverso da zero soltanto se fallisce il runner stesso.

Per svolgere più run manuali, ripetere il comando cambiando `--model-label` o le condizioni sperimentali. Il parametro identifica gli artefatti ma non seleziona né configura automaticamente il modello. In futuro, risultati con label diverse potranno essere confrontati esternamente mantenendo invariati corpus, seed, configurazione e stato dei repository; l'esecuzione automatica multi-modello non è ancora implementata.
