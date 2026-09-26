---
doc_id: JAVA-RB-001
title: Runbook - checkout-service OutOfMemoryError
type: Confluence
application: Java
space: SUPPORT-JAVA
owner: Neha Kapoor (Java Platform L3)
last_updated: 2026-07-25
---
## Symptoms
Pods of checkout-service restart with OOMKilled or logs show java.lang.OutOfMemoryError: Java heap space.

## Diagnosis
1. Check restarts with `kubectl get pods -n shop -l app=checkout-service`.
2. Capture a heap histogram before restart with `kubectl exec -n shop <pod> -- jcmd 1 GC.class_histogram`.
3. Look for com.shop.cart.CartCacheEntry dominating the histogram (known unbounded cache issue).

## Resolution
1. Perform a rolling restart with `kubectl rollout restart deployment/checkout-service -n shop` (production restart, needs standard change approval).
2. Confirm the setting cart.cache.maxEntries is set to 50000 in the config map; if missing, raise to L3 (config change).

## Verification
No new OOM events for 30 minutes and heap usage stays below 75%.
