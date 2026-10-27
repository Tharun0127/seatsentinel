"""Booking strategies. Each module books seats in a different way:

- naive.py        BROKEN on purpose: check-then-act with no locking.
- pessimistic.py  SELECT ... FOR UPDATE inside a transaction.
- optimistic.py   version column + retry on conflict.
- redis_lock.py   distributed lock with SET NX PX + Lua release.

They all share the request/result types and errors defined in common.py.
"""
