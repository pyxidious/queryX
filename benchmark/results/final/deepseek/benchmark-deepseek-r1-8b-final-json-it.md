# QueryX – Report del benchmark

## Metadati

| Metrica | Valore |
| --- | --- |
| Model label | deepseek-r1-8b-final-json |
| Timestamp UTC | 2026-07-23T18:23:24.972253+00:00 |
| Base URL | http://127.0.0.1:8000 |
| File dei casi | /app/benchmark/cases.json |
| Casi logici | 72 |
| Esecuzioni | 100 |
| Query eseguite | 53 |

## Sintesi

Le metriche seguenti derivano esclusivamente dagli artefatti strutturati del benchmark, senza valutazioni soggettive.

La result accuracy riguarda esclusivamente le esecuzioni dotate di `expected_result`.

- Pass rate: 69.44%
- Accuratezza classificazione: 70.83%
- Accuratezza selezione backend: 72.73%
- Tasso piani validi: 72.73%
- Accuratezza esecuzione: 69.23%
- Tasso risultati verificati: 37.50%
- Accuratezza risultati: 60.47%
- Tasso consistenza ripetizioni: 78.57%
- Tasso consistenza semantica: 58.33%
- Tasso rifiuto prudente: 81.82%
- Tasso allucinazioni strutturali: 0.00%
- Tasso timeout: 0.00%
- Tasso errori: 22.00%

## Metriche complessive

| Metrica | Valore |
| --- | --- |
| Pass rate | 69.44% |
| Accuratezza classificazione | 70.83% |
| Accuratezza selezione backend | 72.73% |
| Tasso piani validi | 72.73% |
| Accuratezza esecuzione | 69.23% |
| Tasso risultati verificati | 37.50% |
| Accuratezza risultati | 60.47% |
| Tasso consistenza ripetizioni | 78.57% |
| Tasso consistenza semantica | 58.33% |
| Tasso rifiuto prudente | 81.82% |
| Tasso allucinazioni strutturali | 0.00% |
| Tasso timeout | 0.00% |
| Tasso errori | 22.00% |

## Latenze

| Metrica | Mediana (ms) | p95 (ms) |
| --- | --- | --- |
| planning | 5397.27 | 9729.24 |
| execution | 18.06 | 81.09 |
| explanation | 368.22 | 1041.34 |
| total | 5985.20 | 11246.71 |

La latenza è un indicatore osservabile del costo computazionale, non una misura diretta di CPU, RAM, VRAM o consumo energetico.

## Risultati per backend

| Backend | Casi | Pass rate | Tasso piani validi | Accuratezza esecuzione | Esecuzioni verificate | Accuratezza risultati | Tasso consistenza semantica | Tasso errori |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb | 17 | 58.82% | 58.82% | 50.00% | 10 | 0.00% | 25.00% | 40.00% |
| mongodb | 23 | 78.26% | 82.61% | 78.26% | 24 | 75.00% | 66.67% | 17.14% |
| mysql | 15 | 73.33% | 73.33% | 73.33% | 9 | 88.89% | 100.00% | 15.79% |

## Risultati per operation type

| Operation type | Casi | Pass rate | Esecuzioni verificate | Accuratezza risultati | Tasso errori |
| --- | --- | --- | --- | --- | --- |
| aggregation | 7 | 57.14% | 6 | 50.00% | 22.22% |
| count | 10 | 80.00% | 14 | 85.71% | 6.25% |
| filter | 12 | 91.67% | 0 | n/d | 7.14% |
| group_by | 11 | 81.82% | 17 | 64.71% | 28.57% |
| multi_asset | 3 | 0.00% | 5 | 0.00% | 80.00% |
| projection | 4 | 50.00% | 0 | n/d | 50.00% |
| sort | 2 | 50.00% | 1 | 0.00% | 50.00% |
| temporal_grouping | 4 | 75.00% | 0 | n/d | 16.67% |
| top_k | 2 | 50.00% | 0 | n/d | 50.00% |
| uncertainty | 8 | 50.00% | 0 | n/d | 20.00% |
| unsupported_analysis | 9 | 77.78% | 0 | n/d | 9.09% |

## Risultati per difficoltà

| Difficoltà | Casi | Pass rate | Accuratezza risultati | Tasso errori |
| --- | --- | --- | --- | --- |
| easy | 23 | 73.91% | 75.00% | 17.14% |
| hard | 18 | 55.56% | 40.00% | 38.46% |
| medium | 31 | 74.19% | 66.67% | 15.38% |

## Consistenza temporale

- Casi ripetuti: 14
- Tasso consistenza ripetizioni: 78.57%

| case_id | Ripetizioni | Classificazione consistente | Backend consistente | Piano consistente | Risultato consistente | Consistenza completa |
| --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | 3 | sì | sì | sì | no | no |
| duckdb_orders_by_month | 3 | sì | sì | sì | n/d | sì |
| duckdb_revenue_by_category | 3 | sì | sì | sì | no | no |
| mysql_count_paid | 3 | sì | sì | sì | sì | sì |
| mongodb_count_profiles | 3 | sì | sì | sì | sì | sì |
| mongodb_events_by_type | 3 | sì | sì | sì | sì | sì |
| mongodb_events_item_quantity_count | 3 | sì | sì | sì | sì | sì |
| mongodb_quantity_by_sku | 3 | sì | sì | sì | sì | sì |
| mongodb_profiles_by_role | 3 | sì | sì | sì | no | no |
| uncertain_best_customers | 3 | sì | sì | sì | n/d | sì |
| unanswerable_profit | 3 | sì | sì | sì | n/d | sì |
| robust_duckdb_status | 3 | sì | sì | sì | n/d | sì |
| robust_mysql_average | 3 | sì | sì | sì | n/d | sì |
| robust_mongodb_newsletter | 3 | sì | sì | sì | n/d | sì |

Un caso può essere consistente pur fallendo in tutte le ripetizioni.

## Robustezza semantica

- Equivalence group: 12
- Tasso consistenza semantica: 58.33%

| Equivalence group | Casi | Classificazione consistente | Backend consistente | Operazione consistente | Risultato consistente | Pass rate gruppo | Consistenza completa |
| --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_category_revenue | 3 | no | sì | sì | sì | 0.00% | no |
| duckdb_delivered | 3 | sì | sì | sì | n/d | 100.00% | sì |
| duckdb_orders_monthly | 4 | no | no | no | n/d | 75.00% | no |
| duckdb_orders_status | 3 | no | no | no | sì | 66.67% | no |
| mongodb_events_type | 3 | sì | sì | sì | sì | 100.00% | sì |
| mongodb_items_quantity_gte_2_documents | 2 | sì | sì | sì | sì | 100.00% | sì |
| mongodb_newsletter_enabled | 3 | sì | sì | sì | n/d | 100.00% | sì |
| mongodb_profiles_by_role | 2 | no | no | no | no | 50.00% | no |
| mongodb_profiles_count | 3 | no | no | no | no | 33.33% | no |
| mongodb_quantity_by_sku | 2 | sì | sì | sì | sì | 100.00% | sì |
| mysql_average_total | 3 | sì | sì | sì | sì | 100.00% | sì |
| mysql_paid_count | 3 | sì | sì | sì | sì | 100.00% | sì |

## Ground truth

- Casi con risultato verificato: 27
- Esecuzioni con risultato verificato: 43
- Tasso risultati verificati: 37.50%
- Accuratezza risultati: 60.47%

Il denominatore della result accuracy include soltanto le esecuzioni verificate.

### Risultati per backend

| Metrica | Esecuzioni verificate | Accuratezza risultati |
| --- | --- | --- |
| duckdb | 10 | 0.00% |
| mongodb | 24 | 75.00% |
| mysql | 9 | 88.89% |

### Risultati per operation type

| Metrica | Esecuzioni verificate | Accuratezza risultati |
| --- | --- | --- |
| aggregation | 6 | 50.00% |
| count | 14 | 85.71% |
| group_by | 17 | 64.71% |
| multi_asset | 5 | 0.00% |
| sort | 1 | 0.00% |

## Casi falliti

| case_id | Categoria | Operation type | Difficoltà | Classificazione osservata | Backend osservato | Codice errore | Piano valido | Eseguito | Risultato corrispondente | Motivo |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | duckdb | group_by | easy | n/d | n/d | invalid_logical_plan | no | no | no | classificazione errata |
| duckdb_revenue_by_category | duckdb | multi_asset | hard | n/d | n/d | invalid_logical_plan | no | no | no | classificazione errata |
| duckdb_status_sorted | duckdb | sort | medium | n/d | n/d | invalid_logical_plan | no | no | no | classificazione errata |
| duckdb_monthly_duckdb_variant | duckdb | temporal_grouping | medium | n/d | n/d | invalid_logical_plan | no | no | n/d | classificazione errata |
| duckdb_items_price_sum | duckdb | aggregation | medium | n/d | n/d | invalid_logical_plan | no | no | no | classificazione errata |
| duckdb_revenue_top_categories | duckdb | multi_asset | hard | unanswerable | n/d | n/d | no | no | no | classificazione errata |
| mysql_sum_total | mysql | aggregation | easy | unanswerable | n/d | n/d | no | no | no | classificazione errata |
| mysql_pending_rows | mysql | filter | easy | n/d | n/d | invalid_logical_plan | no | no | n/d | classificazione errata |
| mysql_order_projection | mysql | projection | easy | n/d | n/d | invalid_logical_plan | no | no | n/d | classificazione errata |
| mysql_top_five_orders | mysql | top_k | medium | n/d | n/d | invalid_logical_plan | no | no | n/d | classificazione errata |
| mongodb_profile_projection | mongodb | projection | easy | n/d | n/d | invalid_logical_plan | no | no | n/d | classificazione errata |
| mongodb_item_quantity_gte_2_sum | mongodb_array | aggregation | hard | n/d | n/d | invalid_logical_plan | no | no | no | classificazione errata |
| mongodb_profiles_by_role | mongodb_array | group_by | hard | n/d | n/d | invalid_logical_plan | no | no | no | classificazione errata |
| uncertain_unspecified_criterion | uncertainty | uncertainty | easy | unanswerable | n/d | n/d | no | no | n/d | classificazione errata |
| incomplete_missing_threshold | uncertainty | uncertainty | medium | n/d | n/d | invalid_logical_plan | no | no | n/d | classificazione errata |
| incomplete_unspecified_orders_source | uncertainty | uncertainty | hard | n/d | n/d | invalid_logical_plan | no | no | n/d | classificazione errata |
| incomplete_sort_criterion | uncertainty | uncertainty | medium | unanswerable | n/d | n/d | no | no | n/d | classificazione errata |
| unanswerable_conversion_rate | missing_data | unsupported_analysis | hard | ambiguous | n/d | n/d | no | no | n/d | classificazione errata |
| unanswerable_mysql_join | missing_data | unsupported_analysis | hard | n/d | n/d | invalid_logical_plan | no | no | n/d | classificazione errata |
| rephrase_mongodb_profiles | reformulation | count | medium | n/d | n/d | invalid_logical_plan | no | no | no | classificazione errata |
| robust_duckdb_revenue | robustness | multi_asset | hard | n/d | n/d | invalid_logical_plan | no | no | no | classificazione errata |
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

- `benchmark-deepseek-r1-8b-final-json-20260723T182324Z.json`
- `benchmark-deepseek-r1-8b-final-json-20260723T182324Z.csv`
- `benchmark-deepseek-r1-8b-final-json-20260723T182324Z.summary.json`
- `benchmark-deepseek-r1-8b-final-json-20260723T182324Z.report.it.md`
- `benchmark-deepseek-r1-8b-final-json-20260723T182324Z.report.en.md`

## Appendice casi

| id | Categoria | Backend atteso | Operation type | Difficoltà | Equivalence group | Ripetizioni | Ground truth presente | Esito |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | duckdb | duckdb | group_by | easy | duckdb_orders_status | 3 | sì | fail |
| duckdb_orders_by_month | duckdb | duckdb | temporal_grouping | hard | duckdb_orders_monthly | 3 | no | pass |
| duckdb_revenue_by_category | duckdb | duckdb | multi_asset | hard | duckdb_category_revenue | 3 | sì | fail |
| duckdb_delivered_filter | duckdb | duckdb | filter | easy | duckdb_delivered | 1 | no | pass |
| duckdb_order_projection | duckdb | duckdb | projection | easy | n/d | 1 | no | pass |
| duckdb_status_sorted | duckdb | duckdb | sort | medium | n/d | 1 | sì | fail |
| duckdb_monthly_file_variant | duckdb | duckdb | temporal_grouping | medium | duckdb_orders_monthly | 1 | no | pass |
| duckdb_monthly_duckdb_variant | duckdb | duckdb | temporal_grouping | medium | duckdb_orders_monthly | 1 | no | fail |
| duckdb_delivered_technical | duckdb | duckdb | filter | medium | duckdb_delivered | 1 | no | pass |
| duckdb_items_price_sum | duckdb | duckdb | aggregation | medium | n/d | 1 | sì | fail |
| duckdb_revenue_top_categories | duckdb | duckdb | multi_asset | hard | duckdb_category_revenue | 1 | sì | fail |
| duckdb_items_price_filter | duckdb | duckdb | filter | medium | n/d | 1 | no | pass |
| mysql_orders_by_status | mysql | mysql | group_by | easy | n/d | 1 | sì | pass |
| mysql_count_paid | mysql | mysql | count | easy | mysql_paid_count | 3 | sì | pass |
| mysql_average_total | mysql | mysql | aggregation | easy | mysql_average_total | 1 | sì | pass |
| mysql_sum_total | mysql | mysql | aggregation | easy | n/d | 1 | sì | fail |
| mysql_total_over_100 | mysql | mysql | filter | easy | n/d | 1 | no | pass |
| mysql_count_pending | mysql | mysql | count | easy | n/d | 1 | sì | pass |
| mysql_paid_count_variant | mysql | mysql | count | medium | mysql_paid_count | 1 | no | pass |
| mysql_pending_rows | mysql | mysql | filter | easy | n/d | 1 | no | fail |
| mysql_order_projection | mysql | mysql | projection | easy | n/d | 1 | no | fail |
| mysql_orders_total_sorted | mysql | mysql | sort | medium | n/d | 1 | no | pass |
| mysql_top_five_orders | mysql | mysql | top_k | medium | n/d | 1 | no | fail |
| mysql_customers_projection | mysql | mysql | projection | easy | n/d | 1 | no | pass |
| mongodb_count_profiles | mongodb | mongodb | count | easy | mongodb_profiles_count | 3 | sì | pass |
| mongodb_english_profiles | mongodb | mongodb | filter | easy | n/d | 1 | no | pass |
| mongodb_newsletter_enabled | mongodb | mongodb | filter | easy | mongodb_newsletter_enabled | 1 | no | pass |
| mongodb_events_by_type | mongodb | mongodb | group_by | easy | mongodb_events_type | 3 | sì | pass |
| mongodb_sum_amount | mongodb | mongodb | aggregation | medium | n/d | 1 | sì | pass |
| mongodb_newsletter_disabled | mongodb | mongodb | filter | medium | n/d | 1 | no | pass |
| mongodb_newsletter_enabled_variant | mongodb | mongodb | filter | medium | mongodb_newsletter_enabled | 1 | no | pass |
| mongodb_profile_projection | mongodb | mongodb | projection | easy | n/d | 1 | no | fail |
| mongodb_events_user_one | mongodb | mongodb | count | medium | n/d | 1 | no | pass |
| mongodb_events_amount_filter | mongodb | mongodb | filter | medium | n/d | 1 | no | pass |
| mongodb_events_by_type_variant | mongodb | mongodb | group_by | medium | mongodb_events_type | 1 | sì | pass |
| mongodb_recent_events | mongodb | mongodb | top_k | hard | n/d | 1 | no | pass |
| mongodb_events_item_quantity_count | mongodb_array | mongodb | count | medium | mongodb_items_quantity_gte_2_documents | 3 | sì | pass |
| mongodb_quantity_by_sku | mongodb_array | mongodb | group_by | hard | mongodb_quantity_by_sku | 3 | sì | pass |
| mongodb_events_item_quantity_count_variant | mongodb_array | mongodb | count | hard | mongodb_items_quantity_gte_2_documents | 1 | sì | pass |
| mongodb_quantity_by_sku_variant | mongodb_array | mongodb | group_by | hard | mongodb_quantity_by_sku | 1 | sì | pass |
| mongodb_item_quantity_gte_2_sum | mongodb_array | mongodb | aggregation | hard | n/d | 1 | sì | fail |
| mongodb_profiles_by_role | mongodb_array | mongodb | group_by | hard | mongodb_profiles_by_role | 3 | sì | fail |
| mongodb_profiles_by_role_variant | mongodb_array | mongodb | group_by | hard | mongodb_profiles_by_role | 1 | sì | pass |
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
| rephrase_duckdb_status | reformulation | duckdb | group_by | medium | duckdb_orders_status | 1 | no | pass |
| rephrase_mysql_average | reformulation | mysql | aggregation | medium | mysql_average_total | 1 | sì | pass |
| rephrase_mongodb_profiles | reformulation | mongodb | count | medium | mongodb_profiles_count | 1 | sì | fail |
| robust_duckdb_status | robustness | duckdb | group_by | medium | duckdb_orders_status | 3 | no | pass |
| robust_duckdb_delivered | robustness | duckdb | filter | medium | duckdb_delivered | 1 | no | pass |
| robust_duckdb_revenue | robustness | duckdb | multi_asset | hard | duckdb_category_revenue | 1 | sì | fail |
| robust_mysql_paid | robustness | mysql | count | medium | mysql_paid_count | 1 | sì | pass |
| robust_mysql_average | robustness | mysql | aggregation | medium | mysql_average_total | 3 | no | pass |
| robust_mongodb_profiles | robustness | mongodb | count | medium | mongodb_profiles_count | 1 | sì | fail |
| robust_mongodb_newsletter | robustness | mongodb | filter | medium | mongodb_newsletter_enabled | 3 | no | pass |
| robust_mongodb_events_type | robustness | mongodb | group_by | medium | mongodb_events_type | 1 | sì | pass |
| robust_duckdb_monthly | robustness | duckdb | temporal_grouping | hard | duckdb_orders_monthly | 1 | no | pass |
