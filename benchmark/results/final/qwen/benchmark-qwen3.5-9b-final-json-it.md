# QueryX – Report del benchmark

## Metadati

| Metrica | Valore |
| --- | --- |
| Model label | qwen3.5-9b-final-json |
| Timestamp UTC | 2026-07-23T17:53:53.589292+00:00 |
| Base URL | http://127.0.0.1:8000 |
| File dei casi | /app/benchmark/cases.json |
| Casi logici | 72 |
| Esecuzioni | 100 |
| Query eseguite | 68 |

## Sintesi

Le metriche seguenti derivano esclusivamente dagli artefatti strutturati del benchmark, senza valutazioni soggettive.

La result accuracy riguarda esclusivamente le esecuzioni dotate di `expected_result`.

- Pass rate: 86.11%
- Accuratezza classificazione: 87.50%
- Accuratezza selezione backend: 92.73%
- Tasso piani validi: 92.73%
- Accuratezza esecuzione: 90.38%
- Tasso risultati verificati: 37.50%
- Accuratezza risultati: 88.37%
- Tasso consistenza ripetizioni: 92.86%
- Tasso consistenza semantica: 66.67%
- Tasso rifiuto prudente: 81.82%
- Tasso allucinazioni strutturali: 0.00%
- Tasso timeout: 0.00%
- Tasso errori: 7.00%

## Metriche complessive

| Metrica | Valore |
| --- | --- |
| Pass rate | 86.11% |
| Accuratezza classificazione | 87.50% |
| Accuratezza selezione backend | 92.73% |
| Tasso piani validi | 92.73% |
| Accuratezza esecuzione | 90.38% |
| Tasso risultati verificati | 37.50% |
| Accuratezza risultati | 88.37% |
| Tasso consistenza ripetizioni | 92.86% |
| Tasso consistenza semantica | 66.67% |
| Tasso rifiuto prudente | 81.82% |
| Tasso allucinazioni strutturali | 0.00% |
| Tasso timeout | 0.00% |
| Tasso errori | 7.00% |

## Latenze

| Metrica | Mediana (ms) | p95 (ms) |
| --- | --- | --- |
| planning | 7410.65 | 13544.42 |
| execution | 18.26 | 81.72 |
| explanation | 577.24 | 1934.59 |
| total | 7903.91 | 15663.38 |

La latenza è un indicatore osservabile del costo computazionale, non una misura diretta di CPU, RAM, VRAM o consumo energetico.

## Risultati per backend

| Backend | Casi | Pass rate | Tasso piani validi | Accuratezza esecuzione | Esecuzioni verificate | Accuratezza risultati | Tasso consistenza semantica | Tasso errori |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb | 17 | 82.35% | 82.35% | 78.57% | 10 | 60.00% | 25.00% | 20.00% |
| mongodb | 23 | 95.65% | 100.00% | 95.65% | 24 | 95.83% | 83.33% | 0.00% |
| mysql | 15 | 93.33% | 93.33% | 93.33% | 9 | 100.00% | 100.00% | 5.26% |

## Risultati per operation type

| Operation type | Casi | Pass rate | Esecuzioni verificate | Accuratezza risultati | Tasso errori |
| --- | --- | --- | --- | --- | --- |
| aggregation | 7 | 100.00% | 6 | 100.00% | 0.00% |
| count | 10 | 90.00% | 14 | 92.86% | 0.00% |
| filter | 12 | 91.67% | 0 | n/d | 7.14% |
| group_by | 11 | 100.00% | 17 | 100.00% | 0.00% |
| multi_asset | 3 | 33.33% | 5 | 20.00% | 80.00% |
| projection | 4 | 100.00% | 0 | n/d | 0.00% |
| sort | 2 | 100.00% | 1 | 100.00% | 0.00% |
| temporal_grouping | 4 | 75.00% | 0 | n/d | 16.67% |
| top_k | 2 | 100.00% | 0 | n/d | 0.00% |
| uncertainty | 8 | 62.50% | 0 | n/d | 10.00% |
| unsupported_analysis | 9 | 77.78% | 0 | n/d | 0.00% |

## Risultati per difficoltà

| Difficoltà | Casi | Pass rate | Accuratezza risultati | Tasso errori |
| --- | --- | --- | --- | --- |
| easy | 23 | 95.65% | 100.00% | 2.86% |
| hard | 18 | 72.22% | 73.33% | 15.38% |
| medium | 31 | 87.10% | 91.67% | 5.13% |

## Consistenza temporale

- Casi ripetuti: 14
- Tasso consistenza ripetizioni: 92.86%

| case_id | Ripetizioni | Classificazione consistente | Backend consistente | Piano consistente | Risultato consistente | Consistenza completa |
| --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | 3 | sì | sì | sì | sì | sì |
| duckdb_orders_by_month | 3 | sì | sì | sì | n/d | sì |
| duckdb_revenue_by_category | 3 | sì | sì | sì | no | no |
| mysql_count_paid | 3 | sì | sì | sì | sì | sì |
| mongodb_count_profiles | 3 | sì | sì | sì | sì | sì |
| mongodb_events_by_type | 3 | sì | sì | sì | sì | sì |
| mongodb_events_item_quantity_count | 3 | sì | sì | sì | sì | sì |
| mongodb_quantity_by_sku | 3 | sì | sì | sì | sì | sì |
| mongodb_profiles_by_role | 3 | sì | sì | sì | sì | sì |
| uncertain_best_customers | 3 | sì | sì | sì | n/d | sì |
| unanswerable_profit | 3 | sì | sì | sì | n/d | sì |
| robust_duckdb_status | 3 | sì | sì | sì | n/d | sì |
| robust_mysql_average | 3 | sì | sì | sì | n/d | sì |
| robust_mongodb_newsletter | 3 | sì | sì | sì | n/d | sì |

Un caso può essere consistente pur fallendo in tutte le ripetizioni.

## Robustezza semantica

- Equivalence group: 12
- Tasso consistenza semantica: 66.67%

| Equivalence group | Casi | Classificazione consistente | Backend consistente | Operazione consistente | Risultato consistente | Pass rate gruppo | Consistenza completa |
| --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_category_revenue | 3 | no | no | no | no | 33.33% | no |
| duckdb_delivered | 3 | sì | sì | sì | n/d | 100.00% | sì |
| duckdb_orders_monthly | 4 | no | no | no | n/d | 75.00% | no |
| duckdb_orders_status | 3 | sì | sì | sì | sì | 100.00% | no |
| mongodb_events_type | 3 | sì | sì | sì | sì | 100.00% | sì |
| mongodb_items_quantity_gte_2_documents | 2 | sì | sì | sì | sì | 100.00% | sì |
| mongodb_newsletter_enabled | 3 | sì | sì | sì | n/d | 100.00% | sì |
| mongodb_profiles_by_role | 2 | sì | sì | sì | sì | 100.00% | sì |
| mongodb_profiles_count | 3 | sì | sì | no | no | 66.67% | no |
| mongodb_quantity_by_sku | 2 | sì | sì | sì | sì | 100.00% | sì |
| mysql_average_total | 3 | sì | sì | sì | sì | 100.00% | sì |
| mysql_paid_count | 3 | sì | sì | sì | sì | 100.00% | sì |

## Ground truth

- Casi con risultato verificato: 27
- Esecuzioni con risultato verificato: 43
- Tasso risultati verificati: 37.50%
- Accuratezza risultati: 88.37%

Il denominatore della result accuracy include soltanto le esecuzioni verificate.

### Risultati per backend

| Metrica | Esecuzioni verificate | Accuratezza risultati |
| --- | --- | --- |
| duckdb | 10 | 60.00% |
| mongodb | 24 | 95.83% |
| mysql | 9 | 100.00% |

### Risultati per operation type

| Metrica | Esecuzioni verificate | Accuratezza risultati |
| --- | --- | --- |
| aggregation | 6 | 100.00% |
| count | 14 | 92.86% |
| group_by | 17 | 100.00% |
| multi_asset | 5 | 20.00% |
| sort | 1 | 100.00% |

## Casi falliti

| case_id | Categoria | Operation type | Difficoltà | Classificazione osservata | Backend osservato | Codice errore | Piano valido | Eseguito | Risultato corrispondente | Motivo |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_revenue_by_category | duckdb | multi_asset | hard | n/d | n/d | invalid_logical_plan | no | no | no | classificazione errata |
| duckdb_monthly_duckdb_variant | duckdb | temporal_grouping | medium | n/d | n/d | invalid_logical_plan | no | no | n/d | classificazione errata |
| duckdb_revenue_top_categories | duckdb | multi_asset | hard | n/d | n/d | invalid_logical_plan | no | no | no | classificazione errata |
| mysql_pending_rows | mysql | filter | easy | n/d | n/d | invalid_logical_plan | no | no | n/d | classificazione errata |
| ambiguous_suspicious_events | uncertainty | uncertainty | medium | n/d | n/d | invalid_classification | no | no | n/d | classificazione errata |
| incomplete_missing_threshold | uncertainty | uncertainty | medium | answerable | mysql | n/d | sì | no | n/d | classificazione errata |
| incomplete_unspecified_orders_source | uncertainty | uncertainty | hard | answerable | mysql | n/d | sì | no | n/d | classificazione errata |
| unanswerable_conversion_rate | missing_data | unsupported_analysis | hard | ambiguous | n/d | n/d | no | no | n/d | classificazione errata |
| unanswerable_mysql_join | missing_data | unsupported_analysis | hard | answerable | mysql | n/d | sì | no | n/d | classificazione errata |
| robust_mongodb_profiles | robustness | count | medium | answerable | mongodb | n/d | sì | sì | no | risultato non corrispondente |

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

- `benchmark-qwen3.5-9b-final-json-20260723T175353Z.json`
- `benchmark-qwen3.5-9b-final-json-20260723T175353Z.csv`
- `benchmark-qwen3.5-9b-final-json-20260723T175353Z.summary.json`
- `benchmark-qwen3.5-9b-final-json-20260723T175353Z.report.it.md`
- `benchmark-qwen3.5-9b-final-json-20260723T175353Z.report.en.md`

## Appendice casi

| id | Categoria | Backend atteso | Operation type | Difficoltà | Equivalence group | Ripetizioni | Ground truth presente | Esito |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | duckdb | duckdb | group_by | easy | duckdb_orders_status | 3 | sì | pass |
| duckdb_orders_by_month | duckdb | duckdb | temporal_grouping | hard | duckdb_orders_monthly | 3 | no | pass |
| duckdb_revenue_by_category | duckdb | duckdb | multi_asset | hard | duckdb_category_revenue | 3 | sì | fail |
| duckdb_delivered_filter | duckdb | duckdb | filter | easy | duckdb_delivered | 1 | no | pass |
| duckdb_order_projection | duckdb | duckdb | projection | easy | n/d | 1 | no | pass |
| duckdb_status_sorted | duckdb | duckdb | sort | medium | n/d | 1 | sì | pass |
| duckdb_monthly_file_variant | duckdb | duckdb | temporal_grouping | medium | duckdb_orders_monthly | 1 | no | pass |
| duckdb_monthly_duckdb_variant | duckdb | duckdb | temporal_grouping | medium | duckdb_orders_monthly | 1 | no | fail |
| duckdb_delivered_technical | duckdb | duckdb | filter | medium | duckdb_delivered | 1 | no | pass |
| duckdb_items_price_sum | duckdb | duckdb | aggregation | medium | n/d | 1 | sì | pass |
| duckdb_revenue_top_categories | duckdb | duckdb | multi_asset | hard | duckdb_category_revenue | 1 | sì | fail |
| duckdb_items_price_filter | duckdb | duckdb | filter | medium | n/d | 1 | no | pass |
| mysql_orders_by_status | mysql | mysql | group_by | easy | n/d | 1 | sì | pass |
| mysql_count_paid | mysql | mysql | count | easy | mysql_paid_count | 3 | sì | pass |
| mysql_average_total | mysql | mysql | aggregation | easy | mysql_average_total | 1 | sì | pass |
| mysql_sum_total | mysql | mysql | aggregation | easy | n/d | 1 | sì | pass |
| mysql_total_over_100 | mysql | mysql | filter | easy | n/d | 1 | no | pass |
| mysql_count_pending | mysql | mysql | count | easy | n/d | 1 | sì | pass |
| mysql_paid_count_variant | mysql | mysql | count | medium | mysql_paid_count | 1 | no | pass |
| mysql_pending_rows | mysql | mysql | filter | easy | n/d | 1 | no | fail |
| mysql_order_projection | mysql | mysql | projection | easy | n/d | 1 | no | pass |
| mysql_orders_total_sorted | mysql | mysql | sort | medium | n/d | 1 | no | pass |
| mysql_top_five_orders | mysql | mysql | top_k | medium | n/d | 1 | no | pass |
| mysql_customers_projection | mysql | mysql | projection | easy | n/d | 1 | no | pass |
| mongodb_count_profiles | mongodb | mongodb | count | easy | mongodb_profiles_count | 3 | sì | pass |
| mongodb_english_profiles | mongodb | mongodb | filter | easy | n/d | 1 | no | pass |
| mongodb_newsletter_enabled | mongodb | mongodb | filter | easy | mongodb_newsletter_enabled | 1 | no | pass |
| mongodb_events_by_type | mongodb | mongodb | group_by | easy | mongodb_events_type | 3 | sì | pass |
| mongodb_sum_amount | mongodb | mongodb | aggregation | medium | n/d | 1 | sì | pass |
| mongodb_newsletter_disabled | mongodb | mongodb | filter | medium | n/d | 1 | no | pass |
| mongodb_newsletter_enabled_variant | mongodb | mongodb | filter | medium | mongodb_newsletter_enabled | 1 | no | pass |
| mongodb_profile_projection | mongodb | mongodb | projection | easy | n/d | 1 | no | pass |
| mongodb_events_user_one | mongodb | mongodb | count | medium | n/d | 1 | no | pass |
| mongodb_events_amount_filter | mongodb | mongodb | filter | medium | n/d | 1 | no | pass |
| mongodb_events_by_type_variant | mongodb | mongodb | group_by | medium | mongodb_events_type | 1 | sì | pass |
| mongodb_recent_events | mongodb | mongodb | top_k | hard | n/d | 1 | no | pass |
| mongodb_events_item_quantity_count | mongodb_array | mongodb | count | medium | mongodb_items_quantity_gte_2_documents | 3 | sì | pass |
| mongodb_quantity_by_sku | mongodb_array | mongodb | group_by | hard | mongodb_quantity_by_sku | 3 | sì | pass |
| mongodb_events_item_quantity_count_variant | mongodb_array | mongodb | count | hard | mongodb_items_quantity_gte_2_documents | 1 | sì | pass |
| mongodb_quantity_by_sku_variant | mongodb_array | mongodb | group_by | hard | mongodb_quantity_by_sku | 1 | sì | pass |
| mongodb_item_quantity_gte_2_sum | mongodb_array | mongodb | aggregation | hard | n/d | 1 | sì | pass |
| mongodb_profiles_by_role | mongodb_array | mongodb | group_by | hard | mongodb_profiles_by_role | 3 | sì | pass |
| mongodb_profiles_by_role_variant | mongodb_array | mongodb | group_by | hard | mongodb_profiles_by_role | 1 | sì | pass |
| uncertain_best_customers | uncertainty | n/d | uncertainty | easy | n/d | 3 | no | pass |
| uncertain_best_orders | uncertainty | n/d | uncertainty | easy | n/d | 1 | no | pass |
| uncertain_unspecified_criterion | uncertainty | n/d | uncertainty | easy | n/d | 1 | no | pass |
| ambiguous_important_profiles | uncertainty | n/d | uncertainty | medium | n/d | 1 | no | pass |
| ambiguous_suspicious_events | uncertainty | n/d | uncertainty | medium | n/d | 1 | no | fail |
| incomplete_missing_threshold | uncertainty | n/d | uncertainty | medium | n/d | 1 | no | fail |
| incomplete_unspecified_orders_source | uncertainty | n/d | uncertainty | hard | n/d | 1 | no | fail |
| incomplete_sort_criterion | uncertainty | n/d | uncertainty | medium | n/d | 1 | no | pass |
| unanswerable_profit | uncertainty | n/d | unsupported_analysis | easy | n/d | 3 | no | pass |
| unanswerable_margin | uncertainty | n/d | unsupported_analysis | easy | n/d | 1 | no | pass |
| unanswerable_costs | uncertainty | n/d | unsupported_analysis | easy | n/d | 1 | no | pass |
| unanswerable_conversion_rate | missing_data | n/d | unsupported_analysis | hard | n/d | 1 | no | fail |
| unanswerable_churn | missing_data | n/d | unsupported_analysis | hard | n/d | 1 | no | pass |
| unanswerable_sentiment | missing_data | n/d | unsupported_analysis | medium | n/d | 1 | no | pass |
| unanswerable_fraud_risk | missing_data | n/d | unsupported_analysis | hard | n/d | 1 | no | pass |
| unanswerable_forecast | missing_data | n/d | unsupported_analysis | hard | n/d | 1 | no | pass |
| unanswerable_mysql_join | missing_data | n/d | unsupported_analysis | hard | n/d | 1 | no | fail |
| rephrase_duckdb_status | reformulation | duckdb | group_by | medium | duckdb_orders_status | 1 | no | pass |
| rephrase_mysql_average | reformulation | mysql | aggregation | medium | mysql_average_total | 1 | sì | pass |
| rephrase_mongodb_profiles | reformulation | mongodb | count | medium | mongodb_profiles_count | 1 | sì | pass |
| robust_duckdb_status | robustness | duckdb | group_by | medium | duckdb_orders_status | 3 | no | pass |
| robust_duckdb_delivered | robustness | duckdb | filter | medium | duckdb_delivered | 1 | no | pass |
| robust_duckdb_revenue | robustness | duckdb | multi_asset | hard | duckdb_category_revenue | 1 | sì | pass |
| robust_mysql_paid | robustness | mysql | count | medium | mysql_paid_count | 1 | sì | pass |
| robust_mysql_average | robustness | mysql | aggregation | medium | mysql_average_total | 3 | no | pass |
| robust_mongodb_profiles | robustness | mongodb | count | medium | mongodb_profiles_count | 1 | sì | fail |
| robust_mongodb_newsletter | robustness | mongodb | filter | medium | mongodb_newsletter_enabled | 3 | no | pass |
| robust_mongodb_events_type | robustness | mongodb | group_by | medium | mongodb_events_type | 1 | sì | pass |
| robust_duckdb_monthly | robustness | duckdb | temporal_grouping | hard | duckdb_orders_monthly | 1 | no | pass |
