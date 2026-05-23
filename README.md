# IUN GitOps — Ressources IUN-spécifiques (Approche B)

Repository GitOps du programme **IUN Sénégal**. Géré par **Argo CD** (OpenShift GitOps Operator) avec le pattern **app-of-apps** et **Kustomize** pour les overlays par environnement.

Cible : OpenShift Container Platform 4.16.29, cluster partagé `https://api.origins.heritage.africa:6443`.

---

## 0. Architecture : Approche B (cohabitation sur cluster partagé)

**Décision archi du 2026-05-23** — confirmée par diagnostic cluster :

Le cluster cible est **partagé** entre plusieurs équipes/tenants. Sur les **11 Operators socle** identifiés au §4 du rapport `EVALUATION_OPENSHIFT_v4.16.md`, **9 sont déjà installés** par d'autres équipes :

| #  | Operator                          | Statut cluster        | Géré par Argo CD IUN ? |
|----|-----------------------------------|-----------------------|------------------------|
| 1  | OpenShift GitOps (Argo CD)        | installé              | non (prérequis)        |
| 2  | OpenShift Pipelines (Tekton)      | installé              | non (prérequis)        |
| 3  | OpenShift Service Mesh **3**      | **absent** (SM2 v2.6.15 présent) | non — backlog post-PoC |
| 4  | Red Hat Build of Keycloak         | installé              | non (prérequis)        |
| 5  | CloudNativePG                     | installé              | non (prérequis)        |
| 6  | AMQ Streams (Kafka)               | installé              | non (prérequis)        |
| 7  | OpenShift Logging (Loki + Vector) | installé              | non (prérequis)        |
| 8  | OpenShift Data Foundation         | installé              | non (prérequis)        |
| 9  | cert-manager                      | installé              | non (prérequis)        |
| 10 | External Secrets Operator         | installé              | non (prérequis)        |
| 11 | Red Hat Advanced Cluster Security | **absent** (ns `stackrox` vide)   | non — backlog post-PoC |

**Bonus repéré** : Vault Secrets Operator v1.4.0 est installé sur le cluster — à noter pour la migration future de la gestion des secrets (cohabitation possible avec ESO).

### Approche B : Argo CD IUN ne gère QUE les ressources IUN

> Notre instance Argo CD dédiée (`iun-argocd` dans `iun-gitops`) ne crée **aucune** `Subscription`, `OperatorGroup` ou `Namespace` lié à un Operator socle. Ce repo gère exclusivement les **CR métier IUN** :
>
> - `KeycloakRealmImport` du realm IUN (sur RHBK existant)
> - `Cluster` PostgreSQL IUN (sur CNPG existant)
> - `Kafka` cluster IUN (sur AMQ Streams existant)
> - `Deployment` / `Service` / `Route` de l'API IUN

```
                +------------------------------------------------+
                | Cluster OCP 4.16.29 (api.origins.heritage...)  |
                +------------------------------------------------+
                |                                                |
                | Operators socle (cluster-wide / multi-tenant)  |
                |   géré par d'autres équipes — pas par ce repo  |
                |   +- openshift-gitops-operator                 |
                |   +- openshift-pipelines-operator-rh           |
                |   +- rhbk-operator                             |
                |   +- cloudnative-pg                            |
                |   +- amq-streams                               |
                |   +- cluster-logging                           |
                |   +- odf-operator                              |
                |   +- cert-manager-operator                     |
                |   +- external-secrets-operator                 |
                |   +- vault-secrets-operator (bonus)            |
                |                                                |
                | iun-gitops    (notre instance Argo CD dédiée)  |
                |   +- iun-argocd                                |
                |        +- AppProject iun-platform              |
                |        +- Root Application iun-root-<env>      |
                |        +- Applications IUN (à venir, phase 2)  |
                |                                                |
                +------------------------------------------------+
```

### Pourquoi Approche B et pas Approche A (gestion des 11 Operators)

- **Cohabitation propre sur cluster partagé** : ne pas écraser/diverger les Subscriptions déjà appliquées par d'autres équipes (risque de conflit de channels, de version, de `installPlanApproval`).
- **RBAC clean** : nos Applications n'ont besoin que de droits sur les CR métier IUN, pas sur `operators.coreos.com/Subscription`.
- **Pas de duplication de responsabilité** : la cinématique upgrade des Operators reste chez les équipes qui les ont installés.
- **Démarrage plus rapide** : pas besoin d'attendre 11 réconciliations OLM avant de pouvoir poser un Cluster Postgres.

### Backlog Operators (post-PoC)

- **Service Mesh 3** — SM2 v2.6.15 est présent. Décision installation SM3 reportée tant que la cohabitation SM2/SM3 n'est pas tranchée avec les autres tenants. À discuter avec SRE OCP.
- **RHACS** — namespace `stackrox` vide. Le plan envisage une **alternative OSS** (StackRox community) pour réduire la dépendance à la souscription Red Hat ; à arbitrer post-PoC.

---

## 1. Arborescence

```
gitops/
├── bootstrap/
│   ├── 00-iun-gitops-namespace.yaml    Namespace iun-gitops
│   ├── 01-iun-argocd.yaml              CR ArgoCD iun-argocd (instance dédiée)
│   ├── 02-iun-rbac.yaml                ClusterRoleBindings pour le SA controller (PoC)
│   ├── 03-root-app-project.yaml        AppProject iun-platform
│   ├── 04-root-application.yaml        Root Application (app-of-apps)
│   └── kustomization.yaml
│
├── apps/
│   ├── iun/                       Applications Argo CD IUN-spécifiques (Approche B)
│   │   ├── kustomization.yaml     (liste vide à ce stade — voir README)
│   │   └── README.md
│   └── iun-api/                   (legacy README — sera consolidé dans apps/iun)
│
├── components/                    Bases Kustomize des CR métier IUN
│   ├── iun-api/base/              placeholder — Deployment/Service/Route API .NET
│   ├── cnpg-cluster/base/         placeholder — Cluster Postgres IUN
│   ├── keycloak-realm/base/       placeholder — Realm RHBK IUN
│   └── kafka-cluster/base/        placeholder — Kafka cluster IUN
│
├── environments/{dev,staging,prod}/kustomization.yaml
│     (chaque overlay agrège apps/iun + patches env-spécifiques)
│
├── Bootstrap-IUN.ps1     Amorce l'instance Argo CD dédiée
├── Get-ArgoCD-Admin.ps1  Récupère URL UI + mot de passe admin
├── Test-IUNCluster.ps1   Précheck cluster (read-only)
├── .gitignore
└── README.md
```

### Pattern de déploiement (Approche B)

```
   bootstrap/04-root-application.yaml  (Root Application "iun-root-<env>")
              |   namespace: iun-gitops   (instance dédiée)
              v   source.path = environments/<env>
   environments/<env>/kustomization.yaml
              |
              v   resources += ../../apps/iun
   apps/iun/kustomization.yaml         (vide pour l'instant — phase 2)
              |
              v   futures Applications (iun-cnpg, iun-kafka, iun-keycloak-realm, iun-api)
   components/<composant>/base/        CR métier (Cluster, Kafka, Deployment, …)
```

---

## 2. Composants IUN (phase 2)

| Composant         | Path Kustomize                  | Operator prérequis (déjà installé) | Namespace cible |
|-------------------|---------------------------------|------------------------------------|-----------------|
| Postgres IUN      | `components/cnpg-cluster/base`  | CloudNativePG                      | `iun-data`      |
| Realm Keycloak    | `components/keycloak-realm/base`| RHBK                               | `iun-iam`       |
| Kafka IUN         | `components/kafka-cluster/base` | AMQ Streams                        | `iun-streaming` |
| API IUN (.NET)    | `components/iun-api/base`       | (Pipelines pour build)             | `iun-api`       |

Les fichiers `kustomization.yaml` de chaque dossier sont des **placeholders** vides à ce stade. Chaque composant sera remplie quand l'équipe applicative aura validé la spec (cf. README local de chaque dossier).

---

## 3. Commandes PowerShell

> **Pré-requis Windows** : `git`, `oc` 4.16+, kubeconfig pointant sur `https://api.origins.heritage.africa:6443`.
>
> **⚠ Certificat API expiré sur ce cluster.** Toutes les commandes `oc` ci-dessous incluent `--insecure-skip-tls-verify=true`. Alternative : faire `oc login --insecure-skip-tls-verify=true …` une fois — la session courante mémorise le flag (`~/.kube/config`). Renouvellement du cert API tracé en §5 "Hardening post-PoC".

### (a) Préchecks (état cluster partagé)

```powershell
Set-Location C:\IUN_APP\gitops
.\Test-IUNCluster.ps1   # rapport markdown + .logs\precheck-YYYYMMDD-HHMMSS.md
```

Sortie attendue (Approche B) : OpenShift GitOps + 8 autres Operators socle remontent en `OK`, Service Mesh 3 et RHACS en `INFO` (non installés).

### (b) Bootstrap de l'instance Argo CD IUN

```powershell
Set-Location C:\IUN_APP\gitops
.\Bootstrap-IUN.ps1 -Environment dev          # ou -DryRun en simulation
```

Le script applique `bootstrap/00..04` + patche la Root Application avec `environments/<env>` et le `repoURL`.

### (c) Récupérer URL UI + mot de passe admin

```powershell
.\Get-ArgoCD-Admin.ps1
```

### (d) Vérifier la synchronisation

```powershell
$TlsBypass = "--insecure-skip-tls-verify=true"

# Liste des Applications IUN (dans iun-gitops)
oc get applications -n iun-gitops $TlsBypass

# Vue d'ensemble santé
oc get applications -n iun-gitops $TlsBypass `
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status

# Forcer un refresh / sync manuel d'une Application (quand elles existeront)
oc annotate application iun-cnpg-cluster -n iun-gitops `
  argocd.argoproj.io/refresh=hard --overwrite $TlsBypass
```

### (e) Promouvoir vers staging / prod

```powershell
.\Bootstrap-IUN.ps1 -Environment staging
# ou
.\Bootstrap-IUN.ps1 -Environment prod
```

### (f) Rollback complet de l'instance IUN

```powershell
$TlsBypass = "--insecure-skip-tls-verify=true"
oc delete application iun-root-dev -n iun-gitops $TlsBypass
oc delete argocd iun-argocd       -n iun-gitops $TlsBypass
oc delete namespace iun-gitops    $TlsBypass
```

---

## 4. Conventions

- **Un seul AppProject** (`iun-platform`) à ce stade ; segmenter par BU quand l'équipe scalera.
- **Pas de Secrets en clair dans Git** : tout passe par External Secrets Operator + Vault (les deux déjà installés cluster), ou Sealed Secrets en transition.
- **Toutes les Applications Argo CD** vivent dans `iun-gitops` (`metadata.namespace`). Leurs `destination.namespace` restent les namespaces métier cibles (`iun-data`, `iun-iam`, etc.).
- **Aucune Subscription Operator dans ce repo** (Approche B) — un PR qui en ajoute une doit être refusé.

---

## 5. Hardening post-PoC (dette technique connue)

À traiter après validation fonctionnelle du PoC, avant bascule staging/prod :

| # | Item                                  | Description                                                                                                                                            | Priorité |
|---|---------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------|----------|
| 1 | Cert API serveur OCP expiré           | Renouveler le cert de `api.origins.heritage.africa:6443` pour supprimer `--insecure-skip-tls-verify=true`. Procédure `oc adm` côté SRE.                | Haute    |
| 2 | SA controller = cluster-admin         | Remplacer le CRB cluster-admin (`bootstrap/02-iun-rbac.yaml`) par un ClusterRole fine-grained `iun-argocd-platform` (limité aux CR métier IUN).        | Haute    |
| 3 | SSO Argo CD désactivé                 | Activer OIDC sur le realm Keycloak IUN une fois provisionné ; retirer le rôle local admin mappé aux `system:cluster-admins`.                           | Moyenne  |
| 4 | HA Argo CD désactivée                 | `spec.ha.enabled: true` sur le CR `iun-argocd` (Redis cluster, controller sharding). Coût ~3x mémoire.                                                 | Moyenne  |
| 5 | sourceRepos restreint                 | Déjà restreint à `https://github.com/enguser-1/iun-gitops*`. À durcir encore après migration vers Git on-prem (GitLab souverain / Gitea).              | Basse    |
| 6 | Pipeline de validation Kustomize      | Tekton job `kustomize build environments/dev \| oc apply --dry-run=server -f -` sur chaque PR.                                                         | Basse    |
| 7 | **Service Mesh 3**                    | Décider de l'installation SM3 (cohabitation SM2/SM3 ou bascule). Backlog Approche B.                                                                   | Moyenne  |
| 8 | **RHACS (ou OSS StackRox)**           | Décider et installer le scanner CVE/runtime security. Plan OSS RHACS référencé dans `EVALUATION_OPENSHIFT_v4.16.md`.                                   | Moyenne  |
| 9 | **Migration Vault Secrets Operator**  | Vault Secrets Operator v1.4.0 est présent (bonus cluster). Évaluer migration depuis ESO ou cohabitation ESO+VSO selon les CR existantes.               | Basse    |

---

## 6. Phase 2 (hors scope PoC)

- Remplir `components/iun-api/base/` avec Deployment + Service + Route de l'API .NET conteneurisée.
- Remplir `components/cnpg-cluster/base/` avec `Cluster` Postgres IUN + ScheduledBackup ODF.
- Remplir `components/keycloak-realm/base/` avec realm `iun` + clients OIDC initiaux.
- Remplir `components/kafka-cluster/base/` avec `Kafka` (KRaft) + `KafkaTopic` + `KafkaUser`.
- Ajouter les `Application` Argo CD correspondantes dans `apps/iun/kustomization.yaml`.
- Sync-waves Argo CD (annotation `argocd.argoproj.io/sync-wave`) pour ordonner `cnpg-cluster` -> `keycloak-realm` -> `iun-api`.
- Operators phase 2 (à discuter avec SRE OCP) : Cluster Observability, OpenTelemetry, Tempo, Compliance, OADP, KEDA, Serverless.

---

## 7. Références

- `..\EVALUATION_OPENSHIFT_v4.16.md` — §4 liste exhaustive des Operators recommandés, §7 plan OSS RHACS.
- `..\SYNTHESE_ET_PLAN_DEMARRAGE_v1.md` — feuille de route programme.
- `..\decisions\` — ADR (la décision Approche B sera tracée ici).
- [Argo CD app-of-apps pattern](https://argo-cd.readthedocs.io/en/stable/operator-manual/cluster-bootstrapping/)
- [OpenShift GitOps — Multiple Argo CD instances](https://docs.openshift.com/gitops/latest/argocd_instance/setting-up-argocd-instance.html)
- [ArgoCD CRD (argoproj.io/v1beta1)](https://argocd-operator.readthedocs.io/en/latest/reference/argocd/)
