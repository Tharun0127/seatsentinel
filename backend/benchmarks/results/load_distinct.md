| Strategy | Hot seat: winners of 200 | Double-booked seats (mixed load) | Throughput | p50 | p95 | p99 | Booked / 409 / 5xx |
|---|---|---|---|---|---|---|---|
| naive | 3 | 0 | 395.3 req/s | 22.9 ms | 415.8 ms | 832.8 ms | 1000 / 0 / 0 |
| pessimistic | 1 | 0 | 237.7 req/s | 132.5 ms | 611.9 ms | 954.3 ms | 1000 / 0 / 0 |
| optimistic | 1 | 0 | 287.5 req/s | 103.8 ms | 556.0 ms | 878.1 ms | 1000 / 0 / 0 |
| redis | 1 | 0 | 294.7 req/s | 96.8 ms | 572.3 ms | 826.3 ms | 1000 / 0 / 0 |
