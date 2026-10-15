# Eval results: `rules-dev`

Run at 2026-10-15 17:10:56 CEST - dataset `dev` - qualifier `rules`

| metric | value |
|---|---|
| cases | 48 |
| intent_accuracy | 1.0 |
| tier_accuracy | 1.0 |
| hot_precision | 1.0 |
| hot_recall | 1.0 |
| review_recall | 1.0 |
| auto_replied_to_non_leads | 0 |
| sent_to_review | 11 |
| fallbacks | 0 |
| latency_p50_s | 0.001 |
| latency_max_s | 0.005 |
| cost_usd_total | 0.0 |

## Tier confusion matrix (rows = expected, columns = predicted)

| expected \ got | cold | hot | other | spam | support | warm |
|---|---|---|---|---|---|---|
| **cold** | 7 |  |  |  |  |  |
| **hot** |  | 14 |  |  |  |  |
| **other** |  |  | 10 |  |  |  |
| **spam** |  |  |  | 4 |  |  |
| **support** |  |  |  |  | 4 |  |
| **warm** |  |  |  |  |  | 9 |

## Misses (0)

| case | expected | got | score | conf | notes |
|---|---|---|---|---|---|
