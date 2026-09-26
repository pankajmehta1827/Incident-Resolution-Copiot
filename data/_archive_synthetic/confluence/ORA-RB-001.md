---
doc_id: ORA-RB-001
title: Runbook - Resolving row lock contention on ORDERS (ORA-00054)
type: Confluence
application: Oracle
space: SUPPORT-ORACLE
owner: Rahul Verma (Oracle L3)
last_updated: 2026-05-10
---
## Symptoms
Order entry users see ORA-00054: resource busy and acquire with NOWAIT specified, or screens hang on save. AWR shows wait event enq: TX - row lock contention on ORDERS.

## Diagnosis
1. Identify blocking sessions with `SELECT blocking_session, sid, serial#, wait_class, seconds_in_wait FROM v$session WHERE blocking_session IS NOT NULL;`
2. Map the blocker to its program and module with `SELECT sid, serial#, program, module, sql_id FROM v$session WHERE sid = <blocking_sid>;`
3. Confirm whether the blocker is the BILL_NIGHTLY batch or an idle application session.

## Resolution
1. If the blocker is an idle application session (status INACTIVE for more than 10 minutes), kill it with `ALTER SYSTEM KILL SESSION '<sid>,<serial#>' IMMEDIATE;` This is a production data-affecting action and needs L2 approval.
2. If the blocker is BILL_NIGHTLY, do not kill it. Ask the batch team to pause online order imports until the batch completes.
3. Verify the wait clears by re-running the blocking session query.

## Verification
No rows returned from the blocking session query and users can save orders.
