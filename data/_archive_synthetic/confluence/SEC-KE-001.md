---
doc_id: SEC-KE-001
title: Known error - Suspicious logins on order-api service account
type: Confluence
application: Java
space: SECOPS
restricted: true
owner: Security Operations
last_updated: 2026-08-01
---
## Summary
Restricted security procedure for handling suspected credential misuse on the order-api service account. Handled only through the security incident process.

## Resolution
1. Rotate the service account credential through the vault. Connection used for verification: jdbc:oracle:thin:svc_order/Secr3tPass!@ordprd-scan:1521/ORDAPP
2. Notify the CISO office.
