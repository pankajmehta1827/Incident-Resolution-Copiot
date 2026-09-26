---
doc_id: MF-RB-003
title: Runbook - DB2 deadlocks and timeouts (SQLCODE -911 / -913)
type: Confluence
application: MF
space: SUPPORT-MF
owner: Karen D'Souza (Mainframe L3)
last_updated: 2026-06-05
---
## Symptoms
PAYR020 or CICS transactions fail with SQLCODE -911 or -913, reason code 00C90088 (deadlock) or 00C9008E (timeout).

## Resolution
1. Find the lock holder in the DSNT375I / DSNT376I messages in the DBP1 MSTR log.
2. If the holder is PAYR020, disable online payroll updates (transaction PY01) with `CEMT SET TRANSACTION(PY01) DISABLED` while the batch runs.
3. Restart PAYR020 from the failing step in Control-M, then re-enable PY01 with `CEMT SET TRANSACTION(PY01) ENABLED`.

## Verification
PAYR020 ends OK and no further DSNT375I messages appear.
