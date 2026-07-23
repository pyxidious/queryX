# QueryX – Benchmark Report

## Metadata

| Metric | Value |
| --- | --- |
| Model label | qwen3.5-9b-100k-json |
| UTC timestamp | 2026-07-23T12:19:49.178162+00:00 |
| Base URL | http://127.0.0.1:8000 |
| Cases file | /app/benchmark/cases.json |
| Logical cases | 65 |
| Executions | 87 |
| Executed queries | 51 |

## Summary

The following metrics derive exclusively from structured benchmark artifacts, without subjective assessments.

Result accuracy applies only to executions with an `expected_result`.

- Pass rate: 70.77%
- Classification accuracy: 75.38%
- Backend selection accuracy: 77.08%
- Valid plan rate: 77.08%
- Execution accuracy: 75.56%
- Result verified rate: 30.77%
- Result accuracy: 86.67%
- Repeat consistency rate: 100.00%
- Semantic consistency rate: 66.67%
- Prudent refusal rate: 81.82%
- Structural hallucination rate: 0.00%
- Timeout rate: 0.00%
- Error rate: 19.54%

## Overall metrics

| Metric | Value |
| --- | --- |
| Pass rate | 70.77% |
| Classification accuracy | 75.38% |
| Backend selection accuracy | 77.08% |
| Valid plan rate | 77.08% |
| Execution accuracy | 75.56% |
| Result verified rate | 30.77% |
| Result accuracy | 86.67% |
| Repeat consistency rate | 100.00% |
| Semantic consistency rate | 66.67% |
| Prudent refusal rate | 81.82% |
| Structural hallucination rate | 0.00% |
| Timeout rate | 0.00% |
| Error rate | 19.54% |

## Latencies

| Metric | Median (ms) | p95 (ms) |
| --- | --- | --- |
| planning | 6603.98 | 10542.93 |
| execution | 15.60 | 68.61 |
| explanation | 1047.13 | 1759.24 |
| total | 8008.65 | 12295.71 |

Latency is an observable indicator of computational cost, not a direct measurement of CPU, RAM, VRAM, or energy consumption.

## Results by backend

| Backend | Cases | Pass rate | Valid plan rate | Execution accuracy | Verified executions | Result accuracy | Semantic consistency rate | Error rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb | 17 | 64.71% | 64.71% | 78.57% | 10 | 100.00% | 75.00% | 40.00% |
| mongodb | 16 | 62.50% | 81.25% | 62.50% | 11 | 63.64% | 33.33% | 13.64% |
| mysql | 15 | 86.67% | 86.67% | 86.67% | 9 | 100.00% | 100.00% | 10.53% |

## Results by operation type

| Operation type | Cases | Pass rate | Verified executions | Result accuracy | Error rate |
| --- | --- | --- | --- | --- | --- |
| aggregation | 6 | 100.00% | 5 | 100.00% | 0.00% |
| count | 8 | 75.00% | 10 | 80.00% | 8.33% |
| filter | 12 | 91.67% | 0 | n/a | 7.14% |
| group_by | 7 | 42.86% | 9 | 77.78% | 30.77% |
| multi_asset | 3 | 100.00% | 5 | 100.00% | 0.00% |
| projection | 4 | 75.00% | 0 | n/a | 25.00% |
| sort | 2 | 100.00% | 1 | 100.00% | 0.00% |
| temporal_grouping | 4 | 0.00% | 0 | n/a | 100.00% |
| top_k | 2 | 0.00% | 0 | n/a | 100.00% |
| uncertainty | 8 | 62.50% | 0 | n/a | 20.00% |
| unsupported_analysis | 9 | 77.78% | 0 | n/a | 0.00% |

## Results by difficulty

| Difficulty | Cases | Pass rate | Result accuracy | Error rate |
| --- | --- | --- | --- | --- |
| easy | 23 | 91.30% | 100.00% | 5.71% |
| hard | 12 | 50.00% | 100.00% | 31.25% |
| medium | 30 | 63.33% | 55.56% | 27.78% |

## Temporal consistency

- Repeated cases: 11
- Repeat consistency rate: 100.00%

| case_id | Repeat count | Classification consistency | Backend consistency | Plan consistency | Result consistency | Full consistency |
| --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | 3 | yes | yes | yes | yes | yes |
| duckdb_orders_by_month | 3 | yes | yes | yes | n/a | yes |
| duckdb_revenue_by_category | 3 | yes | yes | yes | yes | yes |
| mysql_count_paid | 3 | yes | yes | yes | yes | yes |
| mongodb_count_profiles | 3 | yes | yes | yes | yes | yes |
| mongodb_events_by_type | 3 | yes | yes | yes | yes | yes |
| uncertain_best_customers | 3 | yes | yes | yes | n/a | yes |
| unanswerable_profit | 3 | yes | yes | yes | n/a | yes |
| robust_duckdb_status | 3 | yes | yes | yes | n/a | yes |
| robust_mysql_average | 3 | yes | yes | yes | n/a | yes |
| robust_mongodb_newsletter | 3 | yes | yes | yes | n/a | yes |

A case may be consistent while failing in every repetition.

## Semantic robustness

- Equivalence groups: 9
- Semantic consistency rate: 66.67%

| Equivalence group | Cases | Classification consistency | Backend consistency | Operation consistency | Result consistency | Group pass rate | Full consistency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_category_revenue | 3 | yes | yes | yes | yes | 100.00% | yes |
| duckdb_delivered | 3 | yes | yes | yes | n/a | 100.00% | yes |
| duckdb_orders_monthly | 4 | yes | yes | yes | n/a | 0.00% | yes |
| duckdb_orders_status | 3 | no | no | no | yes | 33.33% | no |
| mongodb_events_type | 3 | yes | yes | no | no | 33.33% | no |
| mongodb_newsletter_enabled | 3 | yes | yes | yes | n/a | 100.00% | yes |
| mongodb_profiles_count | 3 | no | no | no | no | 33.33% | no |
| mysql_average_total | 3 | yes | yes | yes | yes | 100.00% | yes |
| mysql_paid_count | 3 | yes | yes | yes | yes | 100.00% | yes |

## Ground truth

- Result verified cases: 20
- Result verified executions: 30
- Result verified rate: 30.77%
- Result accuracy: 86.67%

The result accuracy denominator includes verified executions only.

### Results by backend

| Metric | Verified executions | Result accuracy |
| --- | --- | --- |
| duckdb | 10 | 100.00% |
| mongodb | 11 | 63.64% |
| mysql | 9 | 100.00% |

### Results by operation type

| Metric | Verified executions | Result accuracy |
| --- | --- | --- |
| aggregation | 5 | 100.00% |
| count | 10 | 80.00% |
| group_by | 9 | 77.78% |
| multi_asset | 5 | 100.00% |
| sort | 1 | 100.00% |

## Failed cases

| case_id | Category | Operation type | Difficulty | Observed classification | Observed backend | Error code | Plan valid | Executed | Result match | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_month | duckdb | temporal_grouping | hard | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| duckdb_monthly_file_variant | duckdb | temporal_grouping | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| duckdb_monthly_duckdb_variant | duckdb | temporal_grouping | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| mysql_pending_rows | mysql | filter | easy | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| mysql_top_five_orders | mysql | top_k | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| mongodb_profile_projection | mongodb | projection | easy | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| mongodb_events_by_type_variant | mongodb | group_by | medium | answerable | mongodb | n/a | yes | yes | no | result mismatch |
| mongodb_recent_events | mongodb | top_k | hard | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| ambiguous_suspicious_events | uncertainty | uncertainty | medium | n/a | n/a | invalid_classification | no | no | n/a | incorrect classification |
| incomplete_missing_threshold | uncertainty | uncertainty | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| incomplete_unspecified_orders_source | uncertainty | uncertainty | hard | answerable | mysql | n/a | yes | no | n/a | incorrect classification |
| unanswerable_conversion_rate | missing_data | unsupported_analysis | hard | ambiguous | n/a | n/a | no | no | n/a | incorrect classification |
| unanswerable_mysql_join | missing_data | unsupported_analysis | hard | answerable | mysql | n/a | yes | no | n/a | incorrect classification |
| rephrase_duckdb_status | reformulation | group_by | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| rephrase_mongodb_profiles | reformulation | count | medium | n/a | n/a | invalid_logical_plan | no | no | no | incorrect classification |
| robust_duckdb_status | robustness | group_by | medium | n/a | n/a | invalid_logical_plan | no | no | n/a | incorrect classification |
| robust_mongodb_profiles | robustness | count | medium | answerable | mongodb | n/a | yes | yes | no | result mismatch |
| robust_mongodb_events_type | robustness | group_by | medium | answerable | mongodb | n/a | yes | yes | no | result mismatch |
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

- `benchmark-qwen3.5-9b-100k-json-20260723T121949Z.json`
- `benchmark-qwen3.5-9b-100k-json-20260723T121949Z.csv`
- `benchmark-qwen3.5-9b-100k-json-20260723T121949Z.summary.json`
- `benchmark-qwen3.5-9b-100k-json-20260723T121949Z.report.it.md`
- `benchmark-qwen3.5-9b-100k-json-20260723T121949Z.report.en.md`

## Case appendix

| id | Category | Expected backend | Operation type | Difficulty | Equivalence group | Repeat count | Ground truth present | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| duckdb_orders_by_status | duckdb | duckdb | group_by | easy | duckdb_orders_status | 3 | yes | pass |
| duckdb_orders_by_month | duckdb | duckdb | temporal_grouping | hard | duckdb_orders_monthly | 3 | no | fail |
| duckdb_revenue_by_category | duckdb | duckdb | multi_asset | hard | duckdb_category_revenue | 3 | yes | pass |
| duckdb_delivered_filter | duckdb | duckdb | filter | easy | duckdb_delivered | 1 | no | pass |
| duckdb_order_projection | duckdb | duckdb | projection | easy | n/a | 1 | no | pass |
| duckdb_status_sorted | duckdb | duckdb | sort | medium | n/a | 1 | yes | pass |
| duckdb_monthly_file_variant | duckdb | duckdb | temporal_grouping | medium | duckdb_orders_monthly | 1 | no | fail |
| duckdb_monthly_duckdb_variant | duckdb | duckdb | temporal_grouping | medium | duckdb_orders_monthly | 1 | no | fail |
| duckdb_delivered_technical | duckdb | duckdb | filter | medium | duckdb_delivered | 1 | no | pass |
| duckdb_items_price_sum | duckdb | duckdb | aggregation | medium | n/a | 1 | yes | pass |
| duckdb_revenue_top_categories | duckdb | duckdb | multi_asset | hard | duckdb_category_revenue | 1 | yes | pass |
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
| mysql_top_five_orders | mysql | mysql | top_k | medium | n/a | 1 | no | fail |
| mysql_customers_projection | mysql | mysql | projection | easy | n/a | 1 | no | pass |
| mongodb_count_profiles | mongodb | mongodb | count | easy | mongodb_profiles_count | 3 | yes | pass |
| mongodb_english_profiles | mongodb | mongodb | filter | easy | n/a | 1 | no | pass |
| mongodb_newsletter_enabled | mongodb | mongodb | filter | easy | mongodb_newsletter_enabled | 1 | no | pass |
| mongodb_events_by_type | mongodb | mongodb | group_by | easy | mongodb_events_type | 3 | yes | pass |
| mongodb_sum_amount | mongodb | mongodb | aggregation | medium | n/a | 1 | yes | pass |
| mongodb_newsletter_disabled | mongodb | mongodb | filter | medium | n/a | 1 | no | pass |
| mongodb_newsletter_enabled_variant | mongodb | mongodb | filter | medium | mongodb_newsletter_enabled | 1 | no | pass |
| mongodb_profile_projection | mongodb | mongodb | projection | easy | n/a | 1 | no | fail |
| mongodb_events_user_one | mongodb | mongodb | count | medium | n/a | 1 | no | pass |
| mongodb_events_amount_filter | mongodb | mongodb | filter | medium | n/a | 1 | no | pass |
| mongodb_events_by_type_variant | mongodb | mongodb | group_by | medium | mongodb_events_type | 1 | yes | fail |
| mongodb_recent_events | mongodb | mongodb | top_k | hard | n/a | 1 | no | fail |
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
| rephrase_duckdb_status | reformulation | duckdb | group_by | medium | duckdb_orders_status | 1 | no | fail |
| rephrase_mysql_average | reformulation | mysql | aggregation | medium | mysql_average_total | 1 | yes | pass |
| rephrase_mongodb_profiles | reformulation | mongodb | count | medium | mongodb_profiles_count | 1 | yes | fail |
| robust_duckdb_status | robustness | duckdb | group_by | medium | duckdb_orders_status | 3 | no | fail |
| robust_duckdb_delivered | robustness | duckdb | filter | medium | duckdb_delivered | 1 | no | pass |
| robust_duckdb_revenue | robustness | duckdb | multi_asset | hard | duckdb_category_revenue | 1 | yes | pass |
| robust_mysql_paid | robustness | mysql | count | medium | mysql_paid_count | 1 | yes | pass |
| robust_mysql_average | robustness | mysql | aggregation | medium | mysql_average_total | 3 | no | pass |
| robust_mongodb_profiles | robustness | mongodb | count | medium | mongodb_profiles_count | 1 | yes | fail |
| robust_mongodb_newsletter | robustness | mongodb | filter | medium | mongodb_newsletter_enabled | 3 | no | pass |
| robust_mongodb_events_type | robustness | mongodb | group_by | medium | mongodb_events_type | 1 | yes | fail |
| robust_duckdb_monthly | robustness | duckdb | temporal_grouping | hard | duckdb_orders_monthly | 1 | no | fail |
