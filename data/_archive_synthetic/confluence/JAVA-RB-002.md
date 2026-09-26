---
doc_id: JAVA-RB-002
title: Runbook - order-api timeouts (HTTP 504)
type: Confluence
application: Java
space: SUPPORT-JAVA
owner: Neha Kapoor (Java Platform L3)
last_updated: 2026-06-18
---
## Symptoms
Clients receive HTTP 504 from order-api; logs show SocketTimeoutException: Read timed out or HikariPool-1 - Connection is not available.

## Diagnosis
1. Check pricing-service p99 latency on the Grafana dashboard Shop / Pricing.
2. Check Hikari pool metrics hikaricp_connections_pending for order-api.
3. If Hikari pending connections are high, check Oracle ORDPRD for blocking sessions (runbook ORA-RB-001).

## Resolution
1. If pricing-service latency is the cause, scale pricing-service with `kubectl scale deployment/pricing-service -n shop --replicas=6` (production change, standard change pre-approved up to 8 replicas).
2. If the database is the cause, follow ORA-RB-001 with the Oracle team.

## Verification
order-api p99 latency below 1 s and no 504s for 15 minutes.
