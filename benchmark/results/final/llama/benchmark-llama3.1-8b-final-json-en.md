# QueryX – Benchmark Report

## Metadata

| Metric | Value |
| --- | --- |
| Model label | llama3.1-8b-final-json |
| UTC timestamp | 2026-07-23T18:49:56.651152+00:00 |
| Base URL | http://127.0.0.1:8000 |
| Cases file | /app/benchmark/cases.json |
| Logical cases | 72 |
| Executions | 100 |
| Executed queries | 29 |

## Summary

The following metrics derive exclusively from structured benchmark artifacts, without subjective assessments.

Result accuracy applies only to executions with an `expected_result`.

- Pass rate: 50.00%
- Classification accuracy: 50.00%
- Backend selection accuracy: 40.00%
- Valid plan rate: 40.00%
- Execution accuracy: 40.38%
- Result verified rate: 37.50%
- Result accuracy: 25.58%
- Repeat consistency rate: 57.14%
- Semantic consistency rate: 58.33%
- Prudent refusal rate: 90.91%
- Structural hallucination rate: 0.00%
- Timeout rate: 0.00%
- Error rate: 42.00%

## Overall metrics

| Metric | Value |
| --- | --- |
| Pass rate | 50.00% |
| Classification accuracy | 50.00% |
| Backend selection accuracy | 40.00% |
| Valid plan rate | 40.00% |
| Execution accuracy | 40.38% |
| Result verified rate | 37.50% |
| Result accuracy | 25.58% |
| Repeat consistency rate | 57.14% |
| Semantic consistency rate | 58.33% |
| Prudent refusal rate | 90.91% |
| Structural hallucination rate | 0.00% |
| Timeout rate | 0.00% |
| Error rate | 42.00% |

## Latencies

| Metric | Median (ms) | p95 (ms) |
| --- | --- | --- |
| planning | 8178.63 | 11147.58 |
| execution | 19.65 | 287.25 |
| explanation | 393.45 | 2046.35 |
| total | 10906.87 | 20054.99 |

Latency is an observable indicator of computational cost, not a direct measurement of CPU, RAM, VRAM, or energy consumption.

## Results by backend

| Backend | Cases | Pass rate | Valid plan rate | Execution accuracy | Verified executions | Result accuracy | Semantic consistency rate | Error rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb | 17 | 41.18% | 41.18% | 42.86% | 10 | 10.00% | 50.00% | 48.00% |
| mongodb | 23 | 60.87% | 60.87% | 60.87% | 24 | 41.67% | 66.67% | 34.29% |
| mysql | 15 | 6.67% | 6.67% | 6.67% | 9 | 0.00% | 50.00% | 84.21% |

## Results by operation type

| Operation type | Cases | Pass rate | Verified executions | Result accuracy | Error rate |
| --- | --- | --- | --- | --- | --- |
| aggregation | 7 | 28.57% | 6 | 16.67% | 44.44% |
| count | 10 | 10.00% | 14 | 0.00% | 93.75% |
| filter | 12 | 83.33% | 0 | n/a | 14.29% |
| group_by | 11 | 54.55% | 17 | 52.94% | 38.10% |
| multi_asset | 3 | 0.00% | 5 | 0.00% | 0.00% |
| projection | 4 | 25.00% | 0 | n/a | 75.00% |
| sort | 2 | 50.00% | 1 | 100.00% | 50.00% |
| temporal_grouping | 4 | 0.00% | 0 | n/a | 100.00% |
| top_k | 2 | 50.00% | 0 | n/a | 50.00% |
| uncertainty | 8 | 75.00% | 0 | n/a | 20.00% |
| unsupported_analysis | 9 | 88.89% | 0 | n/a | 0.00% |

## Results by difficulty

| Difficulty | Cases | Pass rate | Result accuracy | Error rate |
| --- | --- | --- | --- | --- |
| easy | 23 | 47.83% | 18.75% | 51.43% |
| hard | 18 | 38.89% | 26.67% | 30.77% |
| medium | 31 | 58.06% | 33.33% | 41.03% |

## Temporal consistency

- Repeated cases: 14
- Repeat consistency rate: 57.14%

| case_id | Repeat count | Classification consistency | Backend consistency | Plan consistency | Result consistency | Full consistency |
| --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | 3 | yes | yes | yes | no | no |
| duckdb_orders_by_month | 3 | yes | yes | yes | n/a | yes |
| duckdb_revenue_by_category | 3 | yes | yes | yes | no | no |
| mysql_count_paid | 3 | yes | yes | yes | no | no |
| mongodb_count_profiles | 3 | yes | yes | yes | no | no |
| mongodb_events_by_type | 3 | yes | yes | yes | yes | yes |
| mongodb_events_item_quantity_count | 3 | yes | yes | yes | no | no |
| mongodb_quantity_by_sku | 3 | yes | yes | yes | no | no |
| mongodb_profiles_by_role | 3 | yes | yes | yes | yes | yes |
| uncertain_best_customers | 3 | yes | yes | yes | n/a | yes |
| unanswerable_profit | 3 | yes | yes | yes | n/a | yes |
| robust_duckdb_status | 3 | yes | yes | yes | n/a | yes |
| robust_mysql_average | 3 | yes | yes | yes | n/a | yes |
| robust_mongodb_newsletter | 3 | yes | yes | yes | n/a | yes |

A case may be consistent while failing in every repetition.

## Semantic robustness

- Equivalence groups: 12
- Semantic consistency rate: 58.33%

| Equivalence group | Cases | Classification consistency | Backend consistency | Operation consistency | Result consistency | Group pass rate | Full consistency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_category_revenue | 3 | no | yes | yes | yes | 0.00% | no |
| duckdb_delivered | 3 | yes | yes | yes | n/a | 100.00% | yes |
| duckdb_orders_monthly | 4 | yes | yes | yes | n/a | 0.00% | yes |
| duckdb_orders_status | 3 | no | no | no | yes | 33.33% | no |
| mongodb_events_type | 3 | yes | yes | yes | yes | 100.00% | yes |
| mongodb_items_quantity_gte_2_documents | 2 | yes | yes | yes | yes | 0.00% | yes |
| mongodb_newsletter_enabled | 3 | yes | yes | yes | n/a | 100.00% | yes |
| mongodb_profiles_by_role | 2 | no | no | no | no | 50.00% | no |
| mongodb_profiles_count | 3 | yes | yes | yes | yes | 0.00% | yes |
| mongodb_quantity_by_sku | 2 | no | no | no | no | 50.00% | no |
| mysql_average_total | 3 | no | no | no | yes | 33.33% | no |
| mysql_paid_count | 3 | yes | yes | yes | yes | 0.00% | yes |

## Ground truth

- Result verified cases: 27
- Result verified executions: 43
- Result verified rate: 37.50%
- Result accuracy: 25.58%

The result accuracy denominator includes verified executions only.

### Results by backend

| Metric | Verified executions | Result accuracy |
| --- | --- | --- |
| duckdb | 10 | 10.00% |
| mongodb | 24 | 41.67% |
| mysql | 9 | 0.00% |

### Results by operation type

| Metric | Verified executions | Result accuracy |
| --- | --- | --- |
| aggregation | 6 | 16.67% |
| count | 14 | 0.00% |
| group_by | 17 | 52.94% |
| multi_asset | 5 | 0.00% |
| sort | 1 | 100.00% |

## Failed cases

| case_id | Category | Operation type | Difficulty | Observed classification | Observed backend | Error code | Plan valid | Executed | Result match | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | duckdb | group_by | easy | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| duckdb_orders_by_month | duckdb | temporal_grouping | hard | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| duckdb_revenue_by_category | duckdb | multi_asset | hard | unanswerable | n/a | n/a | no | no | no | incorrect classification |
| duckdb_monthly_file_variant | duckdb | temporal_grouping | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| duckdb_monthly_duckdb_variant | duckdb | temporal_grouping | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| duckdb_items_price_sum | duckdb | aggregation | medium | unanswerable | n/a | n/a | no | no | no | incorrect classification |
| duckdb_revenue_top_categories | duckdb | multi_asset | hard | ambiguous | n/a | n/a | no | no | no | incorrect classification |
| mysql_orders_by_status | mysql | group_by | easy | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| mysql_count_paid | mysql | count | easy | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| mysql_average_total | mysql | aggregation | easy | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| mysql_sum_total | mysql | aggregation | easy | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| mysql_total_over_100 | mysql | filter | easy | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| mysql_count_pending | mysql | count | easy | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| mysql_paid_count_variant | mysql | count | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| mysql_pending_rows | mysql | filter | easy | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| mysql_order_projection | mysql | projection | easy | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| mysql_orders_total_sorted | mysql | sort | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| mysql_top_five_orders | mysql | top_k | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| mysql_customers_projection | mysql | projection | easy | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| mongodb_count_profiles | mongodb | count | easy | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| mongodb_profile_projection | mongodb | projection | easy | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| mongodb_events_item_quantity_count | mongodb_array | count | medium | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| mongodb_quantity_by_sku | mongodb_array | group_by | hard | unanswerable | n/a | n/a | no | no | no | incorrect classification |
| mongodb_events_item_quantity_count_variant | mongodb_array | count | hard | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| mongodb_item_quantity_gte_2_sum | mongodb_array | aggregation | hard | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| mongodb_profiles_by_role_variant | mongodb_array | group_by | hard | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| incomplete_missing_threshold | uncertainty | uncertainty | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| incomplete_unspecified_orders_source | uncertainty | uncertainty | hard | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| unanswerable_conversion_rate | missing_data | unsupported_analysis | hard | ambiguous | n/a | n/a | no | no | n/a | incorrect classification |
| rephrase_mysql_average | reformulation | aggregation | medium | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| rephrase_mongodb_profiles | reformulation | count | medium | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| robust_duckdb_status | robustness | group_by | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| robust_duckdb_revenue | robustness | multi_asset | hard | unanswerable | n/a | n/a | no | no | no | incorrect classification |
| robust_mysql_paid | robustness | count | medium | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| robust_mongodb_profiles | robustness | count | medium | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| robust_duckdb_monthly | robustness | temporal_grouping | hard | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |

## Limitations

- Ground truth covers only a subset of cases.
- Temporal consistency is measured only on repeated cases.
- The linguistic quality of explanations is not evaluated semantically.
- Latency depends on hardware and model cold start.
- Latency does not directly measure resource consumption.
- Consistency does not imply correctness.
- The benchmark evaluates the current demo datasets.

## Reproducibility

```bash
docker compose up --build -d
make seed
make ground-truth
MODEL_LABEL=<MODEL_LABEL> make benchmark
```

## Produced files

- `benchmark-llama3.1-8b-final-json-20260723T184956Z.json`
- `benchmark-llama3.1-8b-final-json-20260723T184956Z.csv`
- `benchmark-llama3.1-8b-final-json-20260723T184956Z.summary.json`
- `benchmark-llama3.1-8b-final-json-20260723T184956Z.report.it.md`
- `benchmark-llama3.1-8b-final-json-20260723T184956Z.report.en.md`

## Case appendix

| id | Category | Expected backend | Operation type | Difficulty | Equivalence group | Repeat count | Ground truth present | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | duckdb | duckdb | group_by | easy | duckdb_orders_status | 3 | yes | fail |
| duckdb_orders_by_month | duckdb | duckdb | temporal_grouping | hard | duckdb_orders_monthly | 3 | no | fail |
| duckdb_revenue_by_category | duckdb | duckdb | multi_asset | hard | duckdb_category_revenue | 3 | yes | fail |
| duckdb_delivered_filter | duckdb | duckdb | filter | easy | duckdb_delivered | 1 | no | pass |
| duckdb_order_projection | duckdb | duckdb | projection | easy | n/a | 1 | no | pass |
| duckdb_status_sorted | duckdb | duckdb | sort | medium | n/a | 1 | yes | pass |
| duckdb_monthly_file_variant | duckdb | duckdb | temporal_grouping | medium | duckdb_orders_monthly | 1 | no | fail |
| duckdb_monthly_duckdb_variant | duckdb | duckdb | temporal_grouping | medium | duckdb_orders_monthly | 1 | no | fail |
| duckdb_delivered_technical | duckdb | duckdb | filter | medium | duckdb_delivered | 1 | no | pass |
| duckdb_items_price_sum | duckdb | duckdb | aggregation | medium | n/a | 1 | yes | fail |
| duckdb_revenue_top_categories | duckdb | duckdb | multi_asset | hard | duckdb_category_revenue | 1 | yes | fail |
| duckdb_items_price_filter | duckdb | duckdb | filter | medium | n/a | 1 | no | pass |
| mysql_orders_by_status | mysql | mysql | group_by | easy | n/a | 1 | yes | fail |
| mysql_count_paid | mysql | mysql | count | easy | mysql_paid_count | 3 | yes | fail |
| mysql_average_total | mysql | mysql | aggregation | easy | mysql_average_total | 1 | yes | fail |
| mysql_sum_total | mysql | mysql | aggregation | easy | n/a | 1 | yes | fail |
| mysql_total_over_100 | mysql | mysql | filter | easy | n/a | 1 | no | fail |
| mysql_count_pending | mysql | mysql | count | easy | n/a | 1 | yes | fail |
| mysql_paid_count_variant | mysql | mysql | count | medium | mysql_paid_count | 1 | no | fail |
| mysql_pending_rows | mysql | mysql | filter | easy | n/a | 1 | no | fail |
| mysql_order_projection | mysql | mysql | projection | easy | n/a | 1 | no | fail |
| mysql_orders_total_sorted | mysql | mysql | sort | medium | n/a | 1 | no | fail |
| mysql_top_five_orders | mysql | mysql | top_k | medium | n/a | 1 | no | fail |
| mysql_customers_projection | mysql | mysql | projection | easy | n/a | 1 | no | fail |
| mongodb_count_profiles | mongodb | mongodb | count | easy | mongodb_profiles_count | 3 | yes | fail |
| mongodb_english_profiles | mongodb | mongodb | filter | easy | n/a | 1 | no | pass |
| mongodb_newsletter_enabled | mongodb | mongodb | filter | easy | mongodb_newsletter_enabled | 1 | no | pass |
| mongodb_events_by_type | mongodb | mongodb | group_by | easy | mongodb_events_type | 3 | yes | pass |
| mongodb_sum_amount | mongodb | mongodb | aggregation | medium | n/a | 1 | yes | pass |
| mongodb_newsletter_disabled | mongodb | mongodb | filter | medium | n/a | 1 | no | pass |
| mongodb_newsletter_enabled_variant | mongodb | mongodb | filter | medium | mongodb_newsletter_enabled | 1 | no | pass |
| mongodb_profile_projection | mongodb | mongodb | projection | easy | n/a | 1 | no | fail |
| mongodb_events_user_one | mongodb | mongodb | count | medium | n/a | 1 | no | pass |
| mongodb_events_amount_filter | mongodb | mongodb | filter | medium | n/a | 1 | no | pass |
| mongodb_events_by_type_variant | mongodb | mongodb | group_by | medium | mongodb_events_type | 1 | yes | pass |
| mongodb_recent_events | mongodb | mongodb | top_k | hard | n/a | 1 | no | pass |
| mongodb_events_item_quantity_count | mongodb_array | mongodb | count | medium | mongodb_items_quantity_gte_2_documents | 3 | yes | fail |
| mongodb_quantity_by_sku | mongodb_array | mongodb | group_by | hard | mongodb_quantity_by_sku | 3 | yes | fail |
| mongodb_events_item_quantity_count_variant | mongodb_array | mongodb | count | hard | mongodb_items_quantity_gte_2_documents | 1 | yes | fail |
| mongodb_quantity_by_sku_variant | mongodb_array | mongodb | group_by | hard | mongodb_quantity_by_sku | 1 | yes | pass |
| mongodb_item_quantity_gte_2_sum | mongodb_array | mongodb | aggregation | hard | n/a | 1 | yes | fail |
| mongodb_profiles_by_role | mongodb_array | mongodb | group_by | hard | mongodb_profiles_by_role | 3 | yes | pass |
| mongodb_profiles_by_role_variant | mongodb_array | mongodb | group_by | hard | mongodb_profiles_by_role | 1 | yes | fail |
| uncertain_best_customers | uncertainty | n/a | uncertainty | easy | n/a | 3 | no | pass |
| uncertain_best_orders | uncertainty | n/a | uncertainty | easy | n/a | 1 | no | pass |
| uncertain_unspecified_criterion | uncertainty | n/a | uncertainty | easy | n/a | 1 | no | pass |
| ambiguous_important_profiles | uncertainty | n/a | uncertainty | medium | n/a | 1 | no | pass |
| ambiguous_suspicious_events | uncertainty | n/a | uncertainty | medium | n/a | 1 | no | pass |
| incomplete_missing_threshold | uncertainty | n/a | uncertainty | medium | n/a | 1 | no | fail |
| incomplete_unspecified_orders_source | uncertainty | n/a | uncertainty | hard | n/a | 1 | no | fail |
| incomplete_sort_criterion | uncertainty | n/a | uncertainty | medium | n/a | 1 | no | pass |
| unanswerable_profit | uncertainty | n/a | unsupported_analysis | easy | n/a | 3 | no | pass |
| unanswerable_margin | uncertainty | n/a | unsupported_analysis | easy | n/a | 1 | no | pass |
| unanswerable_costs | uncertainty | n/a | unsupported_analysis | easy | n/a | 1 | no | pass |
| unanswerable_conversion_rate | missing_data | n/a | unsupported_analysis | hard | n/a | 1 | no | fail |
| unanswerable_churn | missing_data | n/a | unsupported_analysis | hard | n/a | 1 | no | pass |
| unanswerable_sentiment | missing_data | n/a | unsupported_analysis | medium | n/a | 1 | no | pass |
| unanswerable_fraud_risk | missing_data | n/a | unsupported_analysis | hard | n/a | 1 | no | pass |
| unanswerable_forecast | missing_data | n/a | unsupported_analysis | hard | n/a | 1 | no | pass |
| unanswerable_mysql_join | missing_data | n/a | unsupported_analysis | hard | n/a | 1 | no | pass |
| rephrase_duckdb_status | reformulation | duckdb | group_by | medium | duckdb_orders_status | 1 | no | pass |
| rephrase_mysql_average | reformulation | mysql | aggregation | medium | mysql_average_total | 1 | yes | fail |
| rephrase_mongodb_profiles | reformulation | mongodb | count | medium | mongodb_profiles_count | 1 | yes | fail |
| robust_duckdb_status | robustness | duckdb | group_by | medium | duckdb_orders_status | 3 | no | fail |
| robust_duckdb_delivered | robustness | duckdb | filter | medium | duckdb_delivered | 1 | no | pass |
| robust_duckdb_revenue | robustness | duckdb | multi_asset | hard | duckdb_category_revenue | 1 | yes | fail |
| robust_mysql_paid | robustness | mysql | count | medium | mysql_paid_count | 1 | yes | fail |
| robust_mysql_average | robustness | mysql | aggregation | medium | mysql_average_total | 3 | no | pass |
| robust_mongodb_profiles | robustness | mongodb | count | medium | mongodb_profiles_count | 1 | yes | fail |
| robust_mongodb_newsletter | robustness | mongodb | filter | medium | mongodb_newsletter_enabled | 3 | no | pass |
| robust_mongodb_events_type | robustness | mongodb | group_by | medium | mongodb_events_type | 1 | yes | pass |
| robust_duckdb_monthly | robustness | duckdb | temporal_grouping | hard | duckdb_orders_monthly | 1 | no | fail |
