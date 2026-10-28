# Interview questions: concurrency, answered with SeatSentinel

Each answer is written the way you would say it out loud.

---

### 1. What is a race condition? Show me one in your project.

"A race condition is when the result depends on the timing of two things running at once. My naive booking endpoint is the example. It does 'check, then act': SELECT the seat, see it's available, then UPDATE it to booked. Between the check and the write, the code awaits the database, and other requests run in that gap. They all see 'available', so they all book it.

I kept it in the code on purpose, in `strategies/naive.py`. The test fires 200 concurrent requests at seat A1, and 20 different users are told they got it. The scary part is that if you test it by hand, one click at a time, it works perfectly. That's why these bugs survive code review."

### 2. How does pessimistic locking fix it?

"Pessimistic means I assume there will be a conflict, so I lock first. Inside a transaction I run `SELECT ... FOR UPDATE` on the seat rows. The first request gets the row lock; the other 199 wait on it. When the winner commits, the next one gets the lock, and Postgres re-reads the row for it, so it sees 'booked' and gets a clean 409. Result: exactly one 201 and 199 409s.

I also set `lock_timeout` to 5 seconds. Without it, a stuck transaction would make everyone wait forever. With it, they get 'seat is busy, try again'."

### 3. And optimistic locking?

"Optimistic means I assume conflicts are rare, so I don't lock at all. Every seat has a version number. I read the seat and remember, say, version 0. When I write, I say `UPDATE seats SET status='booked', version=version+1 WHERE id=42 AND version=0`. If someone else wrote first, the version is 1, my update matches zero rows, and I know I lost. Then I roll back, re-read and retry, up to 5 times, with a small random backoff so the losers don't all collide again together. If the re-read shows the seat is booked, I stop right away, because retrying can't help."

### 4. When would you pick optimistic over pessimistic?

"Optimistic when conflicts are rare, or when there's a long gap between reading and writing, like a user editing a form for two minutes. You can't hold a database lock for two minutes. Pessimistic when the same rows are hot, like a seat everyone wants or a stock counter, because optimistic would waste work retrying.

In my benchmark, under contention the three correct strategies were within noise of each other, around 370 to 380 requests a second. With no contention at all, pessimistic was slowest, around 240 versus 290, because it pays for a lock even when nobody competes."

### 5. How does your Redis distributed lock work?

"Before touching the database, a request runs `SET lock:seat:42 <random token> NX PX 5000`. NX means only set it if it doesn't exist, so exactly one request wins. PX 5000 means the lock deletes itself after 5 seconds, so a crashed worker can't block the seat forever. The losers fail fast with a 409. The winner books the seat in Postgres and then releases the lock. It's 'distributed' because all four API workers, or many servers, agree through one Redis."

### 6. Why do you release the lock with a Lua script instead of DEL?

"Because of this exact sequence. Worker A takes the lock, then stalls for 6 seconds, maybe a GC pause. Its lock expires. Worker B takes the lock. Then A wakes up and calls DEL, and it deletes B's lock, so a third worker can now get in while B is still working.

So release has to be 'delete only if the value is still my token'. Doing GET then DEL from Python is check-then-act again. A Lua script runs atomically inside Redis, so nothing can happen between the check and the delete. I have a test that reproduces the stale-holder scenario and checks the new owner's lock survives."

### 7. What if the lock expires while the transaction is still running?

"Then two workers could be in the critical section at once. The textbook fix is a fencing token that the storage checks. In my case the storage is Postgres, and the `UNIQUE (seat_id)` constraint on bookings is the fence: the second insert fails with an IntegrityError, which I turn into a 409. That's why I'd never rely on a Redis lock alone for money or inventory."

### 8. Why have a unique constraint if the locks already work?

"Defence in depth. Locks live in application code, and application code has bugs: someone adds a new endpoint and forgets the lock, or a lock expires. The constraint is enforced by the database whatever the app does. I have a test that runs the broken naive logic against the real bookings table: the app check fails, but Postgres lets exactly one booking through and rejects the rest."

### 9. How do you avoid deadlocks when someone books several seats?

"A deadlock happens when request 1 locks A1 and waits for A2, while request 2 locks A2 and waits for A1. My fix is a global lock order: seat ids are always sorted before locking, in every strategy. Then everyone locks A1 before A2, and the cycle can't form. The test has half the users ask for seats [1,2,3] and half for [3,2,1], all at once, and checks there's exactly one winner and no errors."

### 10. How do the 5-minute holds work?

"A hold is a Redis key, `hold:seat:42`, whose value is the user id, with a 5-minute TTL. When the time runs out, Redis deletes it and the seat shows as free again. There's no cleanup job to forget. Holding several seats is one Lua script that checks all of them first and then sets all of them, so two people picking overlapping seats can't each end up with half. The seat map merges Postgres, which says booked, with Redis, which says held, and the frontend polls it every 2 seconds."

### 11. What if payment succeeds right after the hold expired?

"That's a real edge case, and I handle it at confirm time. If my hold is still there, fine. If it expired but nobody else took the seat, I re-hold it atomically and book it, because the customer paid and I shouldn't punish them for a few seconds. The response says `late_hold_recovered`. If someone else has grabbed it, I mark the payment refunded and return a clear 409. There are tests for both paths."

### 12. What is idempotency and how did you implement it?

"An idempotent operation gives the same result however many times you call it. Payments must be, because clients retry: timeouts, flaky mobile networks, double-clicks. The client sends an `Idempotency-Key` header and reuses it on retry.

On the server I insert a row with that key and status 'in progress', using `ON CONFLICT DO NOTHING` on the primary key. Of 20 simultaneous retries, exactly one inserts and does the work. When it finishes, I store the response, even if it's a 409. Later retries get the stored response back, with an `Idempotent-Replayed` header. If a retry is still in flight, it gets 'still being processed'. If someone reuses the key with a different body, they get a 422. My test fires 20 identical payment requests at once and checks there's exactly one payment row."

### 13. What if a buggy client sends a new key on every retry?

"Then idempotency keys can't help, so the payment itself is protected too. It's a little state machine: charged, confirming, then confirmed or refunded. Moving from charged to confirming is one `UPDATE ... WHERE status = 'charged'`, a compare-and-set. Of ten confirms with ten different keys for the same payment, only one UPDATE matches. The test checks there's one 201, nine 409s and one booking."

### 14. READ COMMITTED vs SERIALIZABLE: which do you use and why?

"I have a demo script for this. Two transactions both read the seat as available, then both write. Under READ COMMITTED, the Postgres default, the second blind UPDATE just overwrites the first: a lost update, double booked. Under SERIALIZABLE, the second one fails with error 40001 and has to retry.

I stay on READ COMMITTED, and make the race impossible with FOR UPDATE, a conditional UPDATE, or a lock, plus the unique constraint. SERIALIZABLE works, but then every caller needs a retry loop, and under a hot on-sale lots of transactions abort. I'd reach for it when the rule spans many rows and can't be a row lock, like 'at most 10 seats per user across all events'."

### 15. How would you design BookMyShow?

"I'd start from what this project does and scale each piece.

Seat selection: holds in Redis with a TTL, all-or-nothing with Lua, exactly like here, sharded by show id so one blockbuster doesn't overload a single node. Booking: the seat inventory per show sits in a relational database, partitioned by show, with `UNIQUE (show_id, seat_id)` as the final guarantee. Concurrency is pessimistic locking on just the selected seats, because on-sales are high contention and critical sections are tiny.

Payments: an external gateway called outside any database transaction, with idempotency keys end to end, a payment state machine, and a reconciliation job for payments stuck in 'confirming'. Late payments get re-held or refunded, as here.

For a big on-sale, put a virtual waiting room in front so you only let in as many users as you have seats. The seat map is read-heavy, so serve it from a cache or a push channel, and accept that it's a couple of seconds stale; holds give the real answer. Then measure: my load test shows correctness first, and then where the throughput limit is."
