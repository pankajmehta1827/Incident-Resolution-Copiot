---
doc_id: JAVA-RB-003
title: Runbook - Failed Argo CD deployment
type: Confluence
application: Java
space: SUPPORT-JAVA
owner: Neha Kapoor (Java Platform L3)
last_updated: 2026-03-30
---
## Symptoms
Argo CD shows the application Degraded or OutOfSync after a release; pods in ImagePullBackOff or failing readiness probes.

## Resolution
1. Check pod events with `kubectl describe pod <pod> -n shop`.
2. For ImagePullBackOff, confirm the image tag exists in the registry; a wrong tag needs the release team to fix the manifest.
3. For readiness failures, roll back to the previous revision with `argocd app rollback shop-<service> <previous-revision>` (production change, needs release manager approval).

## Verification
Argo CD shows Healthy and Synced.
