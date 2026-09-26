---
doc_id: MF-RB-002
title: Runbook - CICS region CICSPRD1 unavailable or short on storage
type: Confluence
application: MF
space: SUPPORT-MF
owner: Karen D'Souza (Mainframe L3)
last_updated: 2026-01-15
---
## Symptoms
Customer service screens hang; CICSPRD1 shows message DFHSM0131 (short on storage) or the region is not responding.

## Diagnosis
1. Check region status with `CEMT I SYS` and look for SOS in the storage manager statistics.
2. Check for looping tasks with `CEMT I TASK` and note transactions with high CPU.

## Resolution
1. Purge the looping task with `CEMT SET TASK(<task number>) PURGE`. Needs L2 approval.
2. If SOS persists, the region must be recycled by L3 under an emergency change (production restart).

## Verification
DFHSM0131 no longer appears and screens respond.
