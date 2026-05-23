# IUN API — Applicatif métier

Ce dossier accueillera, en phase 2, les `Application` Argo CD ciblant l'API IUN
(actuellement .NET dans `C:\IUN_APP\IUN.Api\`).

Plan d'introduction :

1. Conteneuriser `IUN.Api` (Containerfile, multi-stage .NET 8).
2. Construire l'image via OpenShift Pipelines (Tekton) → push vers Quay/registry interne.
3. Ajouter ici une `Application` qui déploie `components/iun-api/base/` (Deployment + Service + Route + ServiceMonitor).
4. Brancher l'authN sur Keycloak (composant `rhbk`) via OIDC.

Squelette minimal à ajouter le moment venu :

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: iun-api
  namespace: openshift-gitops
spec:
  project: iun-platform
  source:
    repoURL: https://CHANGE-ME.example.com/iun/gitops.git
    targetRevision: HEAD
    path: components/iun-api/base
  destination:
    server: https://kubernetes.default.svc
    namespace: iun-api
  syncPolicy:
    automated: { prune: true, selfHeal: true }
    syncOptions: [CreateNamespace=true, ServerSideApply=true]
```
