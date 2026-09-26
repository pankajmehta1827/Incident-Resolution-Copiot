---
doc_id: ORA-RB-003
title: Runbook - RMAN nightly backup failure
type: Confluence
application: Oracle
space: SUPPORT-ORACLE
owner: Former DBA team (unassigned)
last_updated: 2024-11-04
---
## Symptoms
The RMAN backup job for ORDPRD fails with ORA-19502 or ORA-19504 and the backup report shows FAILED.

## Resolution
1. Log in to the backup server bkp01 and check free space on /backup_old/ordprd.
2. Delete obsolete backups with `RMAN> DELETE NOPROMPT OBSOLETE;`
3. Re-run the backup script /home/oracle/scripts/rman_full_v1.sh.

## Verification
The RMAN report shows COMPLETED for the rerun.
