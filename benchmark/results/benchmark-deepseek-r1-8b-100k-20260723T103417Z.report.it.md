# QueryX – Report del benchmark

## Metadati

| Metrica | Valore |
| --- | --- |
| Model label | deepseek-r1-8b-100k |
| Timestamp UTC | 2026-07-23T10:34:17.251536+00:00 |
| Base URL | http://127.0.0.1:8000 |
| File dei casi | /app/benchmark/cases.json |
| Casi logici | 65 |
| Esecuzioni | 87 |
| Query eseguite | 0 |

## Sintesi

Le metriche seguenti derivano esclusivamente dagli artefatti strutturati del benchmark, senza valutazioni soggettive.

La result accuracy riguarda esclusivamente le esecuzioni dotate di `expected_result`.

- Pass rate: 16.92%
- Accuratezza classificazione: 16.92%
- Accuratezza selezione backend: 0.00%
- Tasso piani validi: 0.00%
- Accuratezza esecuzione: 0.00%
- Tasso risultati verificati: 30.77%
- Accuratezza risultati: 0.00%
- Tasso consistenza ripetizioni: 54.55%
- Tasso consistenza semantica: 88.89%
- Tasso rifiuto prudente: 81.82%
- Tasso allucinazioni strutturali: 0.00%
- Tasso timeout: 0.00%
- Tasso errori: 77.01%

## Metriche complessive

| Metrica | Valore |
| --- | --- |
| Pass rate | 16.92% |
| Accuratezza classificazione | 16.92% |
| Accuratezza selezione backend | 0.00% |
| Tasso piani validi | 0.00% |
| Accuratezza esecuzione | 0.00% |
| Tasso risultati verificati | 30.77% |
| Accuratezza risultati | 0.00% |
| Tasso consistenza ripetizioni | 54.55% |
| Tasso consistenza semantica | 88.89% |
| Tasso rifiuto prudente | 81.82% |
| Tasso allucinazioni strutturali | 0.00% |
| Tasso timeout | 0.00% |
| Tasso errori | 77.01% |

## Latenze

| Metrica | Mediana (ms) | p95 (ms) |
| --- | --- | --- |
| planning | 1028.18 | 1630.93 |
| execution | n/d | n/d |
| explanation | n/d | n/d |
| total | 933.72 | 1622.55 |

La latenza è un indicatore osservabile del costo computazionale, non una misura diretta di CPU, RAM, VRAM o consumo energetico.

## Risultati per backend

| Backend | Casi | Pass rate | Tasso piani validi | Accuratezza esecuzione | Esecuzioni verificate | Accuratezza risultati | Tasso consistenza semantica | Tasso errori |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb | 17 | 0.00% | 0.00% | 0.00% | 10 | 0.00% | 75.00% | 96.00% |
| mongodb | 16 | 0.00% | 0.00% | 0.00% | 11 | 0.00% | 100.00% | 100.00% |
| mysql | 15 | 0.00% | 0.00% | 0.00% | 9 | 0.00% | 100.00% | 94.74% |

## Risultati per operation type

| Operation type | Casi | Pass rate | Esecuzioni verificate | Accuratezza risultati | Tasso errori |
| --- | --- | --- | --- | --- | --- |
| aggregation | 6 | 0.00% | 5 | 0.00% | 87.50% |
| count | 8 | 0.00% | 10 | 0.00% | 100.00% |
| filter | 12 | 0.00% | 0 | n/d | 100.00% |
| group_by | 7 | 0.00% | 9 | 0.00% | 100.00% |
| multi_asset | 3 | 0.00% | 5 | 0.00% | 80.00% |
| projection | 4 | 0.00% | 0 | n/d | 100.00% |
| sort | 2 | 0.00% | 1 | 0.00% | 100.00% |
| temporal_grouping | 4 | 0.00% | 0 | n/d | 100.00% |
| top_k | 2 | 0.00% | 0 | n/d | 100.00% |
| uncertainty | 8 | 50.00% | 0 | n/d | 20.00% |
| unsupported_analysis | 9 | 77.78% | 0 | n/d | 9.09% |

## Risultati per difficoltà

| Difficoltà | Casi | Pass rate | Accuratezza risultati | Tasso errori |
| --- | --- | --- | --- | --- |
| easy | 23 | 21.74% | 0.00% | 68.57% |
| hard | 12 | 25.00% | 0.00% | 68.75% |
| medium | 30 | 10.00% | 0.00% | 88.89% |

## Consistenza temporale

- Casi ripetuti: 11
- Tasso consistenza ripetizioni: 54.55%

| case_id | Ripetizioni | Classificazione consistente | Backend consistente | Piano consistente | Risultato consistente | Consistenza completa |
| --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | 3 | sì | sì | sì | no | no |
| duckdb_orders_by_month | 3 | sì | sì | sì | n/d | sì |
| duckdb_revenue_by_category | 3 | sì | sì | sì | no | no |
| mysql_count_paid | 3 | sì | sì | sì | no | no |
| mongodb_count_profiles | 3 | sì | sì | sì | no | no |
| mongodb_events_by_type | 3 | sì | sì | sì | no | no |
| uncertain_best_customers | 3 | sì | sì | sì | n/d | sì |
| unanswerable_profit | 3 | sì | sì | sì | n/d | sì |
| robust_duckdb_status | 3 | sì | sì | sì | n/d | sì |
| robust_mysql_average | 3 | sì | sì | sì | n/d | sì |
| robust_mongodb_newsletter | 3 | sì | sì | sì | n/d | sì |

Un caso può essere consistente pur fallendo in tutte le ripetizioni.

## Robustezza semantica

- Equivalence group: 9
- Tasso consistenza semantica: 88.89%

| Equivalence group | Casi | Classificazione consistente | Backend consistente | Operazione consistente | Risultato consistente | Pass rate gruppo | Consistenza completa |
| --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_category_revenue | 3 | no | sì | sì | sì | 0.00% | no |
| duckdb_delivered | 3 | sì | sì | sì | n/d | 0.00% | sì |
| duckdb_orders_monthly | 4 | sì | sì | sì | n/d | 0.00% | sì |
| duckdb_orders_status | 3 | sì | sì | sì | sì | 0.00% | sì |
| mongodb_events_type | 3 | sì | sì | sì | sì | 0.00% | sì |
| mongodb_newsletter_enabled | 3 | sì | sì | sì | n/d | 0.00% | sì |
| mongodb_profiles_count | 3 | sì | sì | sì | sì | 0.00% | sì |
| mysql_average_total | 3 | sì | sì | sì | sì | 0.00% | sì |
| mysql_paid_count | 3 | sì | sì | sì | sì | 0.00% | sì |

## Ground truth

- Casi con risultato verificato: 20
- Esecuzioni con risultato verificato: 30
- Tasso risultati verificati: 30.77%
- Accuratezza risultati: 0.00%

Il denominatore della result accuracy include soltanto le esecuzioni verificate.

### Risultati per backend

| Metrica | Esecuzioni verificate | Accuratezza risultati |
| --- | --- | --- |
| duckdb | 10 | 0.00% |
| mongodb | 11 | 0.00% |
| mysql | 9 | 0.00% |

### Risultati per operation type

| Metrica | Esecuzioni verificate | Accuratezza risultati |
| --- | --- | --- |
| aggregation | 5 | 0.00% |
| count | 10 | 0.00% |
| group_by | 9 | 0.00% |
| multi_asset | 5 | 0.00% |
| sort | 1 | 0.00% |

## Casi falliti

| case_id | Categoria | Operation type | Difficoltà | Classificazione osservata | Backend osservato | Codice errore | Piano valido | Eseguito | Risultato corrispondente | Motivo |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | duckdb | group_by | easy | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| duckdb_orders_by_month | duckdb | temporal_grouping | hard | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| duckdb_revenue_by_category | duckdb | multi_asset | hard | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| duckdb_delivered_filter | duckdb | filter | easy | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| duckdb_order_projection | duckdb | projection | easy | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| duckdb_status_sorted | duckdb | sort | medium | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| duckdb_monthly_file_variant | duckdb | temporal_grouping | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| duckdb_monthly_duckdb_variant | duckdb | temporal_grouping | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| duckdb_delivered_technical | duckdb | filter | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| duckdb_items_price_sum | duckdb | aggregation | medium | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| duckdb_revenue_top_categories | duckdb | multi_asset | hard | unanswerable | n/d | n/d | no | no | no | classificazione errata |
| duckdb_items_price_filter | duckdb | filter | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mysql_orders_by_status | mysql | group_by | easy | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| mysql_count_paid | mysql | count | easy | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| mysql_average_total | mysql | aggregation | easy | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| mysql_sum_total | mysql | aggregation | easy | unanswerable | n/d | n/d | no | no | no | classificazione errata |
| mysql_total_over_100 | mysql | filter | easy | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mysql_count_pending | mysql | count | easy | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| mysql_paid_count_variant | mysql | count | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mysql_pending_rows | mysql | filter | easy | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mysql_order_projection | mysql | projection | easy | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mysql_orders_total_sorted | mysql | sort | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mysql_top_five_orders | mysql | top_k | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mysql_customers_projection | mysql | projection | easy | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mongodb_count_profiles | mongodb | count | easy | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| mongodb_english_profiles | mongodb | filter | easy | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mongodb_newsletter_enabled | mongodb | filter | easy | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mongodb_events_by_type | mongodb | group_by | easy | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| mongodb_sum_amount | mongodb | aggregation | medium | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| mongodb_newsletter_disabled | mongodb | filter | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mongodb_newsletter_enabled_variant | mongodb | filter | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mongodb_profile_projection | mongodb | projection | easy | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mongodb_events_user_one | mongodb | count | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mongodb_events_amount_filter | mongodb | filter | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| mongodb_events_by_type_variant | mongodb | group_by | medium | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| mongodb_recent_events | mongodb | top_k | hard | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| uncertain_unspecified_criterion | uncertainty | uncertainty | easy | unanswerable | n/d | n/d | no | no | n/d | classificazione errata |
| incomplete_missing_threshold | uncertainty | uncertainty | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| incomplete_unspecified_orders_source | uncertainty | uncertainty | hard | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| incomplete_sort_criterion | uncertainty | uncertainty | medium | unanswerable | n/d | n/d | no | no | n/d | classificazione errata |
| unanswerable_conversion_rate | missing_data | unsupported_analysis | hard | ambiguous | n/d | n/d | no | no | n/d | classificazione errata |
| unanswerable_mysql_join | missing_data | unsupported_analysis | hard | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| rephrase_duckdb_status | reformulation | group_by | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| rephrase_mysql_average | reformulation | aggregation | medium | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| rephrase_mongodb_profiles | reformulation | count | medium | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| robust_duckdb_status | robustness | group_by | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| robust_duckdb_delivered | robustness | filter | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| robust_duckdb_revenue | robustness | multi_asset | hard | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| robust_mysql_paid | robustness | count | medium | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| robust_mysql_average | robustness | aggregation | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| robust_mongodb_profiles | robustness | count | medium | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| robust_mongodb_newsletter | robustness | filter | medium | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |
| robust_mongodb_events_type | robustness | group_by | medium | n/d | n/d | llm_unavailable | no | no | no | classificazione errata |
| robust_duckdb_monthly | robustness | temporal_grouping | hard | n/d | n/d | llm_unavailable | no | no | n/d | classificazione errata |

## Limiti

- La ground truth copre soltanto un sottoinsieme dei casi.
- La consistenza temporale è misurata solo sui casi ripetuti.
- La qualità linguistica delle spiegazioni non è valutata semanticamente.
- La latenza dipende dall’hardware e dal cold start del modello.
- La latenza non misura direttamente il consumo di risorse.
- La consistenza non implica correttezza.
- Il benchmark valuta i dataset demo correnti.

## Riproducibilità

```bash
docker compose up --build -d
make seed
make ground-truth
MODEL_LABEL=<MODEL_LABEL> make benchmark
```

## File prodotti

- `benchmark-deepseek-r1-8b-100k-20260723T103417Z.json`
- `benchmark-deepseek-r1-8b-100k-20260723T103417Z.csv`
- `benchmark-deepseek-r1-8b-100k-20260723T103417Z.summary.json`
- `benchmark-deepseek-r1-8b-100k-20260723T103417Z.report.it.md`
- `benchmark-deepseek-r1-8b-100k-20260723T103417Z.report.en.md`

## Appendice casi

| id | Categoria | Backend atteso | Operation type | Difficoltà | Equivalence group | Ripetizioni | Ground truth presente | Esito |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | duckdb | duckdb | group_by | easy | duckdb_orders_status | 3 | sì | fail |
| duckdb_orders_by_month | duckdb | duckdb | temporal_grouping | hard | duckdb_orders_monthly | 3 | no | fail |
| duckdb_revenue_by_category | duckdb | duckdb | multi_asset | hard | duckdb_category_revenue | 3 | sì | fail |
| duckdb_delivered_filter | duckdb | duckdb | filter | easy | duckdb_delivered | 1 | no | fail |
| duckdb_order_projection | duckdb | duckdb | projection | easy | n/d | 1 | no | fail |
| duckdb_status_sorted | duckdb | duckdb | sort | medium | n/d | 1 | sì | fail |
| duckdb_monthly_file_variant | duckdb | duckdb | temporal_grouping | medium | duckdb_orders_monthly | 1 | no | fail |
| duckdb_monthly_duckdb_variant | duckdb | duckdb | temporal_grouping | medium | duckdb_orders_monthly | 1 | no | fail |
| duckdb_delivered_technical | duckdb | duckdb | filter | medium | duckdb_delivered | 1 | no | fail |
| duckdb_items_price_sum | duckdb | duckdb | aggregation | medium | n/d | 1 | sì | fail |
| duckdb_revenue_top_categories | duckdb | duckdb | multi_asset | hard | duckdb_category_revenue | 1 | sì | fail |
| duckdb_items_price_filter | duckdb | duckdb | filter | medium | n/d | 1 | no | fail |
| mysql_orders_by_status | mysql | mysql | group_by | easy | n/d | 1 | sì | fail |
| mysql_count_paid | mysql | mysql | count | easy | mysql_paid_count | 3 | sì | fail |
| mysql_average_total | mysql | mysql | aggregation | easy | mysql_average_total | 1 | sì | fail |
| mysql_sum_total | mysql | mysql | aggregation | easy | n/d | 1 | sì | fail |
| mysql_total_over_100 | mysql | mysql | filter | easy | n/d | 1 | no | fail |
| mysql_count_pending | mysql | mysql | count | easy | n/d | 1 | sì | fail |
| mysql_paid_count_variant | mysql | mysql | count | medium | mysql_paid_count | 1 | no | fail |
| mysql_pending_rows | mysql | mysql | filter | easy | n/d | 1 | no | fail |
| mysql_order_projection | mysql | mysql | projection | easy | n/d | 1 | no | fail |
| mysql_orders_total_sorted | mysql | mysql | sort | medium | n/d | 1 | no | fail |
| mysql_top_five_orders | mysql | mysql | top_k | medium | n/d | 1 | no | fail |
| mysql_customers_projection | mysql | mysql | projection | easy | n/d | 1 | no | fail |
| mongodb_count_profiles | mongodb | mongodb | count | easy | mongodb_profiles_count | 3 | sì | fail |
| mongodb_english_profiles | mongodb | mongodb | filter | easy | n/d | 1 | no | fail |
| mongodb_newsletter_enabled | mongodb | mongodb | filter | easy | mongodb_newsletter_enabled | 1 | no | fail |
| mongodb_events_by_type | mongodb | mongodb | group_by | easy | mongodb_events_type | 3 | sì | fail |
| mongodb_sum_amount | mongodb | mongodb | aggregation | medium | n/d | 1 | sì | fail |
| mongodb_newsletter_disabled | mongodb | mongodb | filter | medium | n/d | 1 | no | fail |
| mongodb_newsletter_enabled_variant | mongodb | mongodb | filter | medium | mongodb_newsletter_enabled | 1 | no | fail |
| mongodb_profile_projection | mongodb | mongodb | projection | easy | n/d | 1 | no | fail |
| mongodb_events_user_one | mongodb | mongodb | count | medium | n/d | 1 | no | fail |
| mongodb_events_amount_filter | mongodb | mongodb | filter | medium | n/d | 1 | no | fail |
| mongodb_events_by_type_variant | mongodb | mongodb | group_by | medium | mongodb_events_type | 1 | sì | fail |
| mongodb_recent_events | mongodb | mongodb | top_k | hard | n/d | 1 | no | fail |
| uncertain_best_customers | uncertainty | n/d | uncertainty | easy | n/d | 3 | no | pass |
| uncertain_best_orders | uncertainty | n/d | uncertainty | easy | n/d | 1 | no | pass |
| uncertain_unspecified_criterion | uncertainty | n/d | uncertainty | easy | n/d | 1 | no | fail |
| ambiguous_important_profiles | uncertainty | n/d | uncertainty | medium | n/d | 1 | no | pass |
| ambiguous_suspicious_events | uncertainty | n/d | uncertainty | medium | n/d | 1 | no | pass |
| incomplete_missing_threshold | uncertainty | n/d | uncertainty | medium | n/d | 1 | no | fail |
| incomplete_unspecified_orders_source | uncertainty | n/d | uncertainty | hard | n/d | 1 | no | fail |
| incomplete_sort_criterion | uncertainty | n/d | uncertainty | medium | n/d | 1 | no | fail |
| unanswerable_profit | uncertainty | n/d | unsupported_analysis | easy | n/d | 3 | no | pass |
| unanswerable_margin | uncertainty | n/d | unsupported_analysis | easy | n/d | 1 | no | pass |
| unanswerable_costs | uncertainty | n/d | unsupported_analysis | easy | n/d | 1 | no | pass |
| unanswerable_conversion_rate | missing_data | n/d | unsupported_analysis | hard | n/d | 1 | no | fail |
| unanswerable_churn | missing_data | n/d | unsupported_analysis | hard | n/d | 1 | no | pass |
| unanswerable_sentiment | missing_data | n/d | unsupported_analysis | medium | n/d | 1 | no | pass |
| unanswerable_fraud_risk | missing_data | n/d | unsupported_analysis | hard | n/d | 1 | no | pass |
| unanswerable_forecast | missing_data | n/d | unsupported_analysis | hard | n/d | 1 | no | pass |
| unanswerable_mysql_join | missing_data | n/d | unsupported_analysis | hard | n/d | 1 | no | fail |
| rephrase_duckdb_status | reformulation | duckdb | group_by | medium | duckdb_orders_status | 1 | no | fail |
| rephrase_mysql_average | reformulation | mysql | aggregation | medium | mysql_average_total | 1 | sì | fail |
| rephrase_mongodb_profiles | reformulation | mongodb | count | medium | mongodb_profiles_count | 1 | sì | fail |
| robust_duckdb_status | robustness | duckdb | group_by | medium | duckdb_orders_status | 3 | no | fail |
| robust_duckdb_delivered | robustness | duckdb | filter | medium | duckdb_delivered | 1 | no | fail |
| robust_duckdb_revenue | robustness | duckdb | multi_asset | hard | duckdb_category_revenue | 1 | sì | fail |
| robust_mysql_paid | robustness | mysql | count | medium | mysql_paid_count | 1 | sì | fail |
| robust_mysql_average | robustness | mysql | aggregation | medium | mysql_average_total | 3 | no | fail |
| robust_mongodb_profiles | robustness | mongodb | count | medium | mongodb_profiles_count | 1 | sì | fail |
| robust_mongodb_newsletter | robustness | mongodb | filter | medium | mongodb_newsletter_enabled | 3 | no | fail |
| robust_mongodb_events_type | robustness | mongodb | group_by | medium | mongodb_events_type | 1 | sì | fail |
| robust_duckdb_monthly | robustness | duckdb | temporal_grouping | hard | duckdb_orders_monthly | 1 | no | fail |
