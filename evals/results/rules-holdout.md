# Eval results: `rules-holdout`

Run at 2026-10-15 17:10:57 CEST - dataset `holdout` - qualifier `rules`

| metric | value |
|---|---|
| cases | 16 |
| intent_accuracy | 0.938 |
| tier_accuracy | 0.938 |
| hot_precision | 1.0 |
| hot_recall | 1.0 |
| review_recall | 1.0 |
| auto_replied_to_non_leads | 0 |
| sent_to_review | 5 |
| fallbacks | 0 |
| latency_p50_s | 0.001 |
| latency_max_s | 0.005 |
| cost_usd_total | 0.0 |

## Tier confusion matrix (rows = expected, columns = predicted)

| expected \ got | cold | hot | other | spam | support | warm |
|---|---|---|---|---|---|---|
| **cold** | 1 |  | 1 |  |  |  |
| **hot** |  | 4 |  |  |  |  |
| **other** |  |  | 3 |  |  |  |
| **spam** |  |  |  | 2 |  |  |
| **support** |  |  |  |  | 1 |  |
| **warm** |  |  |  |  |  | 4 |

## Misses (1)

| case | expected | got | score | conf | notes |
|---|---|---|---|---|---|
| h-cold-01 | sales_inquiry / cold | other / other | 0 | 0.3 | low confidence (0.30) |
