# Eval results: `rules-dev-before-tuning`

Run at 2026-10-15 16:59:26 CEST - dataset `dev` - qualifier `rules`

| metric | value |
|---|---|
| cases | 48 |
| intent_accuracy | 0.896 |
| tier_accuracy | 0.792 |
| hot_precision | 1.0 |
| hot_recall | 0.929 |
| review_recall | 1.0 |
| auto_replied_to_non_leads | 1 |
| sent_to_review | 13 |
| fallbacks | 0 |
| latency_p50_s | 0.001 |
| latency_max_s | 0.004 |
| cost_usd_total | 0.0 |

## Tier confusion matrix (rows = expected, columns = predicted)

| expected \ got | cold | hot | other | spam | support | warm |
|---|---|---|---|---|---|---|
| **cold** | 7 |  |  |  |  |  |
| **hot** |  | 13 | 1 |  |  |  |
| **other** | 1 |  | 9 |  |  |  |
| **spam** |  |  | 1 | 2 | 1 |  |
| **support** |  |  |  |  | 4 |  |
| **warm** | 5 |  | 1 |  |  | 3 |

## Misses (10)

| case | expected | got | score | conf | notes |
|---|---|---|---|---|---|
| hot-08 | sales_inquiry / hot | job_application / other | 0 | 0.75 |  |
| warm-02 | sales_inquiry / warm | sales_inquiry / cold | 35 | 0.57 | low confidence (0.57) |
| warm-04 | sales_inquiry / warm | sales_inquiry / cold | 35 | 0.71 |  |
| warm-06 | sales_inquiry / warm | other / other | 0 | 0.3 | low confidence (0.30) |
| warm-07 | sales_inquiry / warm | sales_inquiry / cold | 40 | 0.57 | low confidence (0.57) |
| warm-08 | sales_inquiry / warm | sales_inquiry / cold | 35 | 0.64 |  |
| spam-03 | spam / spam | other / other | 0 | 0.3 | low confidence (0.30) |
| spam-04 | spam / spam | support_request / support | 0 | 0.75 |  |
| fr-01 | sales_inquiry / warm | sales_inquiry / cold | 35 | 0.57 | low confidence (0.57) |
| other-02 | other / other | sales_inquiry / cold | 35 | 0.57 | low confidence (0.57) |
