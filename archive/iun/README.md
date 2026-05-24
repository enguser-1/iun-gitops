# apps/iun — Applications Argo CD IUN-spécifiques

Ce dossier remplace l'ancien `apps/socle/`. Il agrège les `Application` Argo CD du programme IUN sur le cluster OpenShift partagé `api.origins.heritage.africa`.

## Contexte : Approche B (cohabitation cluster partagé)

Le diagnostic du 2026-05-23 a confirmé que **9 des 11 Operators socle sont déjà installés** sur le cluster par d'autres équipes (cluster partagé) :

- OpenShift GitOps, OpenShift Pipelines, RHBK, CloudNativePG, AMQ Streams, OpenShift Logging, ODF, cert-manager, External Secrets.

**Décision archi (2026-05-23)** : notre instance Argo CD dédiée `iun-argocd` ne gère **que les ressources IUN-spécifiques**. On ne crée pas de `Subscription`/`OperatorGroup` pour les Operators existants — c'est la responsabilité des équipes qui les ont installés.

Voir `../../README.md` § "Architecture : Approche B".

## Backlog (à remplir en phase 2)

| Application Argo CD | Composant ciblé                | Operator prérequis    | Statut       |
|---------------------|--------------------------------|-----------------------|--------------|
| `iun-keycloak-realm`| `components/keycloak-realm/`   | RHBK (déjà installé)  | placeholder  |
| `iun-cnpg-cluster`  | `components/cnpg-cluster/`     | CNPG (déjà installé)  | placeholder  |
| `iun-kafka-cluster` | `components/kafka-cluster/`    | AMQ Streams (idem)    | placeholder  |
| `iun-api`           | `components/iun-api/`          | (Pipelines pour build)| placeholder  |

## Backlog Operators (post-PoC)

Les 2 Operators manquants ne sont pas gérés ici tant que la décision installation n'est pas tranchée :

- **OpenShift Service Mesh 3** — seul SM2 v2.6.15 présent. À installer en stable v3 quand l'équipe SRE valide la cohabitation SM2/SM3 (ou la bascule).
- **RHACS (Red Hat Advanced Cluster Security)** — namespace `stackrox` vide. Plan OSS RHACS référencé en backlog.

## Pattern futur d'une Application

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: iun-cnpg-cluster
  namespace: iun-gitops
  labels:
    app.kubernetes.io/part-of: iun-platform
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: iun-platform
  source:
    repoURL: https://github.com/enguser-1/iun-gitops.git
    targetRevision: HEAD
    path: components/cnpg-cluster/base
  destination:
    server: https://kubernetes.default.svc
    namespace: iun-api
  syncPolicy:
    automated: { prune: true, selfHeal: true }
    syncOptions: [CreateNamespace=true, ServerSideApply=true]
```
