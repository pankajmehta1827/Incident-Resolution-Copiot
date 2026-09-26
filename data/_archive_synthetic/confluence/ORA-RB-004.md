---
doc_id: ORA-RB-004
title: Runbook - Slow queries after bulk load (stale optimizer statistics)
type: Confluence
application: Oracle
space: SUPPORT-ORACLE
owner: Rahul Verma (Oracle L3)
last_updated: 2026-04-22
---
## Symptoms
Order search screens take more than 30 seconds after the monthly bulk load; execution plans switch from index range scan to full table scan.

## Diagnosis
1. Check statistics age with `SELECT table_name, last_analyzed, stale_stats FROM dba_tab_statistics WHERE owner = 'ORD' AND stale_stats = 'YES';`
2. Compare the current plan with the baseline using `SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR('<sql_id>'));`

## Resolution
1. Gather statistics for the stale tables with `EXEC DBMS_STATS.GATHER_TABLE_STATS('ORD','ORDERS', cascade => TRUE);` Run outside peak hours where possible.
2. If the plan is still wrong, raise to L3 to load the SQL plan baseline.

## Verification
Order search responds in under 3 seconds.
