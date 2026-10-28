| Strategy | Hot seat: winners of 200 | Double-booked seats (mixed load) | Throughput | p50 | p95 | p99 | Booked / 409 / 5xx |
|---|---|---|---|---|---|---|---|
| naive | 4 | 3 | 328.3 req/s | 78.5 ms | 552.4 ms | 804.3 ms | 669 / 1331 / 0 |
| pessimistic | 1 | 0 | 377.4 req/s | 79.9 ms | 395.8 ms | 670.0 ms | 688 / 1312 / 0 |
| optimistic | 1 | 0 | 381.0 req/s | 80.0 ms | 401.6 ms | 623.5 ms | 673 / 1327 / 0 |
| redis | 1 | 0 | 366.9 req/s | 79.1 ms | 434.0 ms | 681.1 ms | 677 / 1323 / 0 |
