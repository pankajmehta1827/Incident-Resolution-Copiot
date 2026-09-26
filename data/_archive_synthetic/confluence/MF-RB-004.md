---
doc_id: MF-RB-004
title: Runbook - Batch job delay and Control-M late alerts
type: Confluence
application: MF
space: SUPPORT-MF
owner: Karen D'Souza (Mainframe L3)
last_updated: 2025-12-12
---
## Symptoms
Control-M raises a LATE alert for the billing or payroll suite; jobs sit in Wait Condition status.

## Resolution
1. Check which condition the job is waiting for in Control-M (Why option).
2. If waiting for PRD.ORA.EXTRACT, confirm with the Oracle team that the extract finished and the file transfer completed.
3. If waiting for an initiator, check job class B initiators with `$DI` and ask operations to start an extra initiator.
4. Do not force-order jobs without batch owner approval.

## Verification
The job starts and the LATE alert clears.
