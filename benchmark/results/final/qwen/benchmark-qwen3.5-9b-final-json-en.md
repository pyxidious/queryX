# QueryX – Benchmark Report

## Metadata

| Metric | Value |
| --- | --- |
| Model label | qwen3.5-9b-final-json |
| UTC timestamp | 2026-07-23T17:53:53.589292+00:00 |
| Base URL | http://127.0.0.1:8000 |
| Cases file | /app/benchmark/cases.json |
| Logical cases | 72 |
| Executions | 100 |
| Executed queries | 68 |

## Summary

The following metrics derive exclusively from structured benchmark artifacts, without subjective assessments.

Result accuracy applies only to executions with an `expected_result`.

- Pass rate: 86.11%
- Classification accuracy: 87.50%
- Backend selection accuracy: 92.73%
- Valid plan rate: 92.73%
- Execution accuracy: 90.38%
- Result verified rate: 37.50%
- Result accuracy: 88.37%
- Repeat consistency rate: 92.86%
- Semantic consistency rate: 66.67%
- Prudent refusal rate: 81.82%
- Structural hallucination rate: 0.00%
- Timeout rate: 0.00%
- Error rate: 7.00%

## Overall metrics

| Metric | Value |
| --- | --- |
| Pass rate | 86.11% |
| Classification accuracy | 87.50% |
| Backend selection accuracy | 92.73% |
| Valid plan rate | 92.73% |
| Execution accuracy | 90.38% |
| Result verified rate | 37.50% |
| Result accuracy | 88.37% |
| Repeat consistency rate | 92.86% |
| Semantic consistency rate | 66.67% |
| Prudent refusal rate | 81.82% |
| Structural hallucination rate | 0.00% |
| Timeout rate | 0.00% |
| Error rate | 7.00% |

## Latencies

| Metric | Median (ms) | p95 (ms) |
| --- | --- | --- |
| planning | 7410.65 | 13544.42 |
| execution | 18.26 | 81.72 |
| explanation | 577.24 | 1934.59 |
| total | 7903.91 | 15663.38 |

Latency is an observable indicator of computational cost, not a direct measurement of CPU, RAM, VRAM, or energy consumption.

## Results by backend

| Backend | Cases | Pass rate | Valid plan rate | Execution accuracy | Verified executions | Result accuracy | Semantic consistency rate | Error rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb | 17 | 82.35% | 82.35% | 78.57% | 10 | 60.00% | 25.00% | 20.00% |
| mongodb | 23 | 95.65% | 100.00% | 95.65% | 24 | 95.83% | 83.33% | 0.00% |
| mysql | 15 | 93.33% | 93.33% | 93.33% | 9 | 100.00% | 100.00% | 5.26% |

## Results by operation type

| Operation type | Cases | Pass rate | Verified executions | Result accuracy | Error rate |
| --- | --- | --- | --- | --- | --- |
| aggregation | 7 | 100.00% | 6 | 100.00% | 0.00% |
| count | 10 | 90.00% | 14 | 92.86% | 0.00% |
| filter | 12 | 91.67% | 0 | n/a | 7.14% |
| group_by | 11 | 100.00% | 17 | 100.00% | 0.00% |
| multi_asset | 3 | 33.33% | 5 | 20.00% | 80.00% |
| projection | 4 | 100.00% | 0 | n/a | 0.00% |
| sort | 2 | 100.00% | 1 | 100.00% | 0.00% |
| temporal_grouping | 4 | 75.00% | 0 | n/a | 16.67% |
| top_k | 2 | 100.00% | 0 | n/a | 0.00% |
| uncertainty | 8 | 62.50% | 0 | n/a | 10.00% |
| unsupported_analysis | 9 | 77.78% | 0 | n/a | 0.00% |

## Results by difficulty

| Difficulty | Cases | Pass rate | Result accuracy | Error rate |
| --- | --- | --- | --- | --- |
| easy | 23 | 95.65% | 100.00% | 2.86% |
| hard | 18 | 72.22% | 73.33% | 15.38% |
| medium | 31 | 87.10% | 91.67% | 5.13% |

## Temporal consistency

- Repeated cases: 14
- Repeat consistency rate: 92.86%

| case_id | Repeat count | Classification consistency | Backend consistency | Plan consistency | Result consistency | Full consistency |
| --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | 3 | yes | yes | yes | yes | yes |
| duckdb_orders_by_month | 3 | yes | yes | yes | n/a | yes |
| duckdb_revenue_by_category | 3 | yes | yes | yes | no | no |
| mysql_count_paid | 3 | yes | yes | yes | yes | yes |
| mongodb_count_profiles | 3 | yes | yes | yes | yes | yes |
| mongodb_events_by_type | 3 | yes | yes | yes | yes | yes |
| mongodb_events_item_quantity_count | 3 | yes | yes | yes | yes | yes |
| mongodb_quantity_by_sku | 3 | yes | yes | yes | yes | yes |
| mongodb_profiles_by_role | 3 | yes | yes | yes | yes | yes |
| uncertain_best_customers | 3 | yes | yes | yes | n/a | yes |
| unanswerable_profit | 3 | yes | yes | yes | n/a | yes |
| robust_duckdb_status | 3 | yes | yes | yes | n/a | yes |
| robust_mysql_average | 3 | yes | yes | yes | n/a | yes |
| robust_mongodb_newsletter | 3 | yes | yes | yes | n/a | yes |

A case may be consistent while failing in every repetition.

## Semantic robustness

- Equivalence groups: 12
- Semantic consistency rate: 66.67%

| Equivalence group | Cases | Classification consistency | Backend consistency | Operation consistency | Result consistency | Group pass rate | Full consistency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_category_revenue | 3 | no | no | no | no | 33.33% | no |
| duckdb_delivered | 3 | yes | yes | yes | n/a | 100.00% | yes |
| duckdb_orders_monthly | 4 | no | no | no | n/a | 75.00% | no |
| duckdb_orders_status | 3 | yes | yes | yes | yes | 100.00% | no |
| mongodb_events_type | 3 | yes | yes | yes | yes | 100.00% | yes |
| mongodb_items_quantity_gte_2_documents | 2 | yes | yes | yes | yes | 100.00% | yes |
| mongodb_newsletter_enabled | 3 | yes | yes | yes | n/a | 100.00% | yes |
| mongodb_profiles_by_role | 2 | yes | yes | yes | yes | 100.00% | yes |
| mongodb_profiles_count | 3 | yes | yes | no | no | 66.67% | no |
| mongodb_quantity_by_sku | 2 | yes | yes | yes | yes | 100.00% | yes |
| mysql_average_total | 3 | yes | yes | yes | yes | 100.00% | yes |
| mysql_paid_count | 3 | yes | yes | yes | yes | 100.00% | yes |

## Ground truth

- Result verified cases: 27
- Result verified executions: 43
- Result verified rate: 37.50%
- Result accuracy: 88.37%

The result accuracy denominator includes verified executions only.

### Results by backend

| Metric | Verified executions | Result accuracy |
| --- | --- | --- |
| duckdb | 10 | 60.00% |
| mongodb | 24 | 95.83% |
| mysql | 9 | 100.00% |

### Results by operation type

| Metric | Verified executions | Result accuracy |
| --- | --- | --- |
| aggregation | 6 | 100.00% |
| count | 14 | 92.86% |
| group_by | 17 | 100.00% |
| multi_asset | 5 | 20.00% |
| sort | 1 | 100.00% |

## Failed cases

| case_id | Category | Operation type | Difficulty | Observed classification | Observed backend | Error code | Plan valid | Executed | Result match | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_revenue_by_category | duckdb | multi_asset | hard | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| duckdb_monthly_duckdb_variant | duckdb | temporal_grouping | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| duckdb_revenue_top_categories | duckdb | multi_asset | hard | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| mysql_pending_rows | mysql | filter | easy | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| ambiguous_suspicious_events | uncertainty | uncertainty | medium | n/a | n/a | invalid_classification | no | no | n/a | incorrect classification |
| incomplete_missing_threshold | uncertainty | uncertainty | medium | answerable | mysql | n/a | yes | no | n/a | incorrect classification |
| incomplete_unspecified_orders_source | uncertainty | uncertainty | hard | answerable | mysql | n/a | yes | no | n/a | incorrect classification |
| unanswerable_conversion_rate | missing_data | unsupported_analysis | hard | ambiguous | n/a | n/a | no | no | n/a | incorrect classification |
| unanswerable_mysql_join | missing_data | unsupported_analysis | hard | answerable | mysql | n/a | yes | no | n/a | incorrect classification |
| robust_mongodb_profiles | robustness | count | medium | answerable | mongodb | n/a | yes | yes | no | result mismatch |

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

- `benchmark-qwen3.5-9b-final-json-20260723T175353Z.json`
- `benchmark-qwen3.5-9b-final-json-20260723T175353Z.csv`
- `benchmark-qwen3.5-9b-final-json-20260723T175353Z.summary.json`
- `benchmark-qwen3.5-9b-final-json-20260723T175353Z.report.it.md`
- `benchmark-qwen3.5-9b-final-json-20260723T175353Z.report.en.md`

## Case appendix

| id | Category | Expected backend | Operation type | Difficulty | Equivalence group | Repeat count | Ground truth present | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | duckdb | duckdb | group_by | easy | duckdb_orders_status | 3 | yes | pass |
| duckdb_orders_by_month | duckdb | duckdb | temporal_grouping | hard | duckdb_orders_monthly | 3 | no | pass |
| duckdb_revenue_by_category | duckdb | duckdb | multi_asset | hard | duckdb_category_revenue | 3 | yes | fail |
| duckdb_delivered_filter | duckdb | duckdb | filter | easy | duckdb_delivered | 1 | no | pass |
| duckdb_order_projection | duckdb | duckdb | projection | easy | n/a | 1 | no | pass |
| duckdb_status_sorted | duckdb | duckdb | sort | medium | n/a | 1 | yes | pass |
| duckdb_monthly_file_variant | duckdb | duckdb | temporal_grouping | medium | duckdb_orders_monthly | 1 | no | pass |
| duckdb_monthly_duckdb_variant | duckdb | duckdb | temporal_grouping | medium | duckdb_orders_monthly | 1 | no | fail |
| duckdb_delivered_technical | duckdb | duckdb | filter | medium | duckdb_delivered | 1 | no | pass |
| duckdb_items_price_sum | duckdb | duckdb | aggregation | medium | n/a | 1 | yes | pass |
| duckdb_revenue_top_categories | duckdb | duckdb | multi_asset | hard | duckdb_category_revenue | 1 | yes | fail |
| duckdb_items_price_filter | duckdb | duckdb | filter | medium | n/a | 1 | no | pass |
| mysql_orders_by_status | mysql | mysql | group_by | easy | n/a | 1 | yes | pass |
| mysql_count_paid | mysql | mysql | count | easy | mysql_paid_count | 3 | yes | pass |
| mysql_average_total | mysql | mysql | aggregation | easy | mysql_average_total | 1 | yes | pass |
| mysql_sum_total | mysql | mysql | aggregation | easy | n/a | 1 | yes | pass |
| mysql_total_over_100 | mysql | mysql | filter | easy | n/a | 1 | no | pass |
| mysql_count_pending | mysql | mysql | count | easy | n/a | 1 | yes | pass |
| mysql_paid_count_variant | mysql | mysql | count | medium | mysql_paid_count | 1 | no | pass |
| mysql_pending_rows | mysql | mysql | filter | easy | n/a | 1 | no | fail |
| mysql_order_projection | mysql | mysql | projection | easy | n/a | 1 | no | pass |
| mysql_orders_total_sorted | mysql | mysql | sort | medium | n/a | 1 | no | pass |
| mysql_top_five_orders | mysql | mysql | top_k | medium | n/a | 1 | no | pass |
| mysql_customers_projection | mysql | mysql | projection | easy | n/a | 1 | no | pass |
| mongodb_count_profiles | mongodb | mongodb | count | easy | mongodb_profiles_count | 3 | yes | pass |
| mongodb_english_profiles | mongodb | mongodb | filter | easy | n/a | 1 | no | pass |
| mongodb_newsletter_enabled | mongodb | mongodb | filter | easy | mongodb_newsletter_enabled | 1 | no | pass |
| mongodb_events_by_type | mongodb | mongodb | group_by | easy | mongodb_events_type | 3 | yes | pass |
| mongodb_sum_amount | mongodb | mongodb | aggregation | medium | n/a | 1 | yes | pass |
| mongodb_newsletter_disabled | mongodb | mongodb | filter | medium | n/a | 1 | no | pass |
| mongodb_newsletter_enabled_variant | mongodb | mongodb | filter | medium | mongodb_newsletter_enabled | 1 | no | pass |
| mongodb_profile_projection | mongodb | mongodb | projection | easy | n/a | 1 | no | pass |
| mongodb_events_user_one | mongodb | mongodb | count | medium | n/a | 1 | no | pass |
| mongodb_events_amount_filter | mongodb | mongodb | filter | medium | n/a | 1 | no | pass |
| mongodb_events_by_type_variant | mongodb | mongodb | group_by | medium | mongodb_events_type | 1 | yes | pass |
| mongodb_recent_events | mongodb | mongodb | top_k | hard | n/a | 1 | no | pass |
| mongodb_events_item_quantity_count | mongodb_array | mongodb | count | medium | mongodb_items_quantity_gte_2_documents | 3 | yes | pass |
| mongodb_quantity_by_sku | mongodb_array | mongodb | group_by | hard | mongodb_quantity_by_sku | 3 | yes | pass |
| mongodb_events_item_quantity_count_variant | mongodb_array | mongodb | count | hard | mongodb_items_quantity_gte_2_documents | 1 | yes | pass |
| mongodb_quantity_by_sku_variant | mongodb_array | mongodb | group_by | hard | mongodb_quantity_by_sku | 1 | yes | pass |
| mongodb_item_quantity_gte_2_sum | mongodb_array | mongodb | aggregation | hard | n/a | 1 | yes | pass |
| mongodb_profiles_by_role | mongodb_array | mongodb | group_by | hard | mongodb_profiles_by_role | 3 | yes | pass |
| mongodb_profiles_by_role_variant | mongodb_array | mongodb | group_by | hard | mongodb_profiles_by_role | 1 | yes | pass |
| uncertain_best_customers | uncertainty | n/a | uncertainty | easy | n/a | 3 | no | pass |
| uncertain_best_orders | uncertainty | n/a | uncertainty | easy | n/a | 1 | no | pass |
| uncertain_unspecified_criterion | uncertainty | n/a | uncertainty | easy | n/a | 1 | no | pass |
| ambiguous_important_profiles | uncertainty | n/a | uncertainty | medium | n/a | 1 | no | pass |
| ambiguous_suspicious_events | uncertainty | n/a | uncertainty | medium | n/a | 1 | no | fail |
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
| unanswerable_mysql_join | missing_data | n/a | unsupported_analysis | hard | n/a | 1 | no | fail |
| rephrase_duckdb_status | reformulation | duckdb | group_by | medium | duckdb_orders_status | 1 | no | pass |
| rephrase_mysql_average | reformulation | mysql | aggregation | medium | mysql_average_total | 1 | yes | pass |
| rephrase_mongodb_profiles | reformulation | mongodb | count | medium | mongodb_profiles_count | 1 | yes | pass |
| robust_duckdb_status | robustness | duckdb | group_by | medium | duckdb_orders_status | 3 | no | pass |
| robust_duckdb_delivered | robustness | duckdb | filter | medium | duckdb_delivered | 1 | no | pass |
| robust_duckdb_revenue | robustness | duckdb | multi_asset | hard | duckdb_category_revenue | 1 | yes | pass |
| robust_mysql_paid | robustness | mysql | count | medium | mysql_paid_count | 1 | yes | pass |
| robust_mysql_average | robustness | mysql | aggregation | medium | mysql_average_total | 3 | no | pass |
| robust_mongodb_profiles | robustness | mongodb | count | medium | mongodb_profiles_count | 1 | yes | fail |
| robust_mongodb_newsletter | robustness | mongodb | filter | medium | mongodb_newsletter_enabled | 3 | no | pass |
| robust_mongodb_events_type | robustness | mongodb | group_by | medium | mongodb_events_type | 1 | yes | pass |
| robust_duckdb_monthly | robustness | duckdb | temporal_grouping | hard | duckdb_orders_monthly | 1 | no | pass |
