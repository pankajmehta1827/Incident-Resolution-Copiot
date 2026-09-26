---
doc_id: ORA-RB-002
title: Runbook - Connection pool exhaustion (ORA-12519 / ORA-12520)
type: Confluence
application: Oracle
space: SUPPORT-ORACLE
owner: Rahul Verma (Oracle L3)
last_updated: 2026-02-18
---
## Symptoms
Applications fail to connect with ORA-12519: TNS:no appropriate service handler found, or ORA-12520. The listener log shows service ORDAPP blocked.

## Diagnosis
1. Check current sessions against the processes limit with `SELECT resource_name, current_utilization, max_utilization, limit_value FROM v$resource_limit WHERE resource_name IN ('processes','sessions');`
2. Count sessions per machine with `SELECT machine, count(*) FROM v$session GROUP BY machine ORDER BY 2 DESC;` to find the app server leaking connections.

## Resolution
1. Ask the owning application team to restart the leaking application server during an approved window (production restart, needs change approval).
2. Inactive sessions older than 60 minutes from the leaking machine may be killed by L2.
3. Raising the processes parameter needs an L3 change request and a database restart; do not change it during an incident.

## Verification
current_utilization for processes falls below 80% of limit_value and new connections succeed.
