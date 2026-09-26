---
doc_id: MF-RB-001
title: Runbook - Batch JCL abends (S0C7, SB37, S806)
type: Confluence
application: MF
space: SUPPORT-MF
owner: Karen D'Souza (Mainframe L3)
last_updated: 2026-07-01
---
## Symptoms
A Control-M job ends NOTOK with abend code S0C7, SB37/SD37 or S806 in the JES2 job log.

## S0C7 data exception
1. Open the JES2 output and note the failing step and the offset in the CEEDUMP.
2. Browse the input file for non-numeric data in packed fields; for PAYR010 this is usually the HR input file PRD.HR.PAYIN.
3. Ask HR to resend a corrected file, or with business approval, move the bad record into PRD.HR.PAYIN.REJECT.
4. Restart the job from the failing step in Control-M using the restart option (FROM step). Restarting a payroll job is a production change and needs L2 approval.

## SB37 / SD37 space abend
1. Identify the output dataset in the IEC030I message.
2. Increase the SPACE secondary allocation in the JCL override member and restart from the failing step.

## S806 module not found
1. Check the STEPLIB concatenation and whether the load module exists in PRD.LOADLIB. Usually caused by a missed promotion; escalate to the application team.

## Verification
The job ends OK and downstream jobs are released in Control-M.
