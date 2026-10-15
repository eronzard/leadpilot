# Eval results: `rules-holdout-before-tuning`

Run at 2026-10-15 16:59:25 CEST - dataset `holdout` - qualifier `rules`

| metric | value |
|---|---|
| cases | 16 |
| intent_accuracy | 0.875 |
| tier_accuracy | 0.625 |
| hot_precision | 1.0 |
| hot_recall | 1.0 |
| review_recall | 1.0 |
| auto_replied_to_non_leads | 0 |
| sent_to_review | 5 |
| fallbacks | 0 |
| latency_p50_s | 0.001 |
| latency_max_s | 0.004 |
| cost_usd_total | 0.0 |

## Tier confusion matrix (rows = expected, columns = predicted)

| expected \ got | cold | hot | other | spam | support | warm |
|---|---|---|---|---|---|---|
| **cold** | 1 |  | 1 |  |  |  |
| **hot** |  | 4 |  |  |  |  |
| **other** |  |  | 3 |  |  |  |
| **spam** |  |  | 1 | 1 |  |  |
| **support** |  |  |  |  | 1 |  |
| **warm** | 4 |  |  |  |  |  |

## Misses (6)

| case | expected | got | score | conf | notes |
|---|---|---|---|---|---|
| h-warm-01 | sales_inquiry / warm | sales_inquiry / cold | 40 | 0.5 | low confidence (0.50) |
| h-warm-02 | sales_inquiry / warm | sales_inquiry / cold | 40 | 0.64 |  |
| h-warm-03 | sales_inquiry / warm | sales_inquiry / cold | 40 | 0.64 |  |
| h-cold-01 | sales_inquiry / cold | other / other | 0 | 0.3 | low confidence (0.30) |
| h-spam-02 | spam / spam | other / other | 0 | 0.3 | low confidence (0.30) |
| h-inject-01 | sales_inquiry / warm | sales_inquiry / cold | 35 | 0.57 | low confidence (0.57) |
