# IUN GitOps — Stack DPG (OpenCRVS / MOSIP / OpenIMIS) sur OCP

Repository GitOps du programme **IUN Sénégal**. Géré par **Argo CD** (instance dédiée `iun-argocd`, dans le namespace `iun-gitops`) avec le pattern **app-of-apps** et **Kustomize** pour les overlays par environnement.

Cible : OpenShift Container Platform 4.16.29, cluster partagé `https://api.origins.heritage.africa:6443`.

> **Pivot stratégique 2026-05-24** — ce repo a été restructuré pour porter la
> **stack DPG mandatée par la TOR officielle v2.0** (`proposition_IUN_Senegal_vf.docx`,
> §3.2 + §4) : **OpenCRVS** (état civil), **MOSIP** (génération IUN), **OpenIMIS**
> (protection sociale). L'ancien scaffolding Quarkus (`apps/iun/`, `components/`)
> est archivé dans `archive/` (cf. `archive/README.md` et `archive/CHANTIERS-OBSOLETES.md`).
>
> Document stratégique de référence : `C:\IUN_APP\architecture\REPENSEE-PROJET-v1.md`
> (§4 architecture cible, §6 plan 30 jours, §5 décisions à arbitrer).

---

## 0. Architecture cible — stack DPG sur cluster OCP partagé

### Les 3 Digital Public Goods

| DPG       | Rôle TOR                                  | Namespace cible    | Source officielle                                | Version visée |
|-----------|-------------------------------------------|--------------------|--------------------------------------------------|---------------|
| **OpenCRVS** | État civil (TOR §3.2 C1, §4.2)         | `opencrvs-{env}`   | `github.com/opencrvs/infrastructure` (Helm)      | **v1.9.12**   |
| **MOSIP**    | Génération IUN 9 chiffres + Verhoeff + ID Auth (TOR §3.2 C2, §4.1, §4.3) | `mosip-{env}`      | `mosip.github.io/mosip-helm` (V3 deployment)     | **1.2.1.0** LTS |
| **OpenIMIS** | Protection sociale CMU/PNBSF/IPRES/CSS (TOR §3.2 C3, §4.4) | `openimis-{env}`   | `github.com/openimis/openimis-dist_dkr` (**docker-compose — pas de Helm officiel**) | latest |

> ⚠ **OpenIMIS** n'a pas de Helm chart officiel — voir `apps/dpg/openimis/README.md`
> pour l'arbitrage en cours (kompose convert vs fork communautaire vs contribution upstream).

### Topologie déploiement

```
                +------------------------------------------------------------+
                | Cluster OCP 4.16.29 (api.origins.heritage.africa:6443)     |
                +------------------------------------------------------------+
                |                                                            |
                | Operators socle (cluster-wide / multi-tenant)              |
                |   géré par d'autres équipes — pas par ce repo              |
                |   +- openshift-gitops-operator       (Argo CD)             |
                |   +- openshift-pipelines-operator    (Tekton)              |
                |   +- rhbk-operator                   (Keycloak)            |
                |   +- cloudnative-pg                  (Postgres)            |
                |   +- amq-streams                     (Kafka)               |
                |   +- cluster-logging                 (Loki + Vector)       |
                |   +- odf-operator                    (ODF/Ceph)            |
                |   +- cert-manager-operator                                 |
                |   +- external-secrets-operator                             |
                |   +- vault-secrets-operator          (bonus, v1.4.0)       |
                |                                                            |
                | iun-gitops    (notre instance Argo CD dédiée)              |
                |   +- iun-argocd                                            |
                |        +- AppProject iun-platform                          |
                |        +- Root Application iun-root-<env>                  |
                |        +- Applications DPG :                               |
                |             - mosip-bootstrap-probe   --> ns mosip-<env>   |
                |             - opencrvs-core           --> ns opencrvs-<env>|
                |             - openimis-core           --> ns openimis-<env>|
                |        +- (à venir) apps/integration :                     |
                |             - 3scale gateway, FastAPI/Camel K adapters     |
                |                                                            |
                +------------------------------------------------------------+
```

### Cohabitation cluster partagé (rappel)

Sur les **11 Operators socle** identifiés au §4 du rapport `EVALUATION_OPENSHIFT_v4.16.md`,
**9 sont déjà installés** par d'autres équipes :

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

> Notre instance Argo CD dédiée (`iun-argocd`) ne crée **aucune** `Subscription`,
> `OperatorGroup` ou `Namespace` lié à un Operator socle. Ce repo gère exclusivement
> les **Applications Argo CD qui déploient les 3 DPG** depuis leurs sources officielles
> upstream, plus la couche d'intégration (à venir).

### Pourquoi cette stack et pas une réécriture custom

- **TOR §3.2 + §4** mandate explicitement OpenCRVS + MOSIP + OpenIMIS.
- **L'IUN est une capacité native de MOSIP** (`mosip.kernel.idgenerator.uin.check-digit-algorithm=VERHOEFF`) — pas un service à coder.
- **0 FCFA de licence logicielle** sur le chemin applicatif (TOR §7 + §1) — les 3 DPG sont open source.
- **80 % des choix d'infrastructure** (OCP 4.x, Service Mesh, Kafka, RHBK, ODF, Vault, Compliance Operator, GitOps Argo CD) **sont confirmés à l'identique par la TOR §5.4** — le socle ne change pas, seule la couche applicative est repensée.

---

## 1. Arborescence

```
gitops/
├── bootstrap/                          (inchangé — instance Argo CD dédiée)
│   ├── 00-iun-gitops-namespace.yaml    Namespace iun-gitops
│   ├── 01-iun-argocd.yaml              CR ArgoCD iun-argocd (instance dédiée)
│   ├── 02-iun-rbac.yaml                ClusterRoleBindings pour le SA controller (PoC)
│   ├── 03-root-app-project.yaml        AppProject iun-platform
│   ├── 04-root-application.yaml        Root Application (app-of-apps)
│   └── kustomization.yaml
│
├── apps/
│   ├── dpg/                            Applications Argo CD des 3 DPG
│   │   ├── mosip/                      MOSIP (UIN generator, ID auth, registration)
│   │   │   ├── application.yaml        Argo CD Application (probe initial)
│   │   │   ├── kustomization.yaml
│   │   │   ├── README.md               Rôle, source, perso Sénégal, prérequis, validation
│   │   │   ├── values/{dev,staging,prod}.yaml
│   │   │   └── manifests/
│   │   │       ├── namespace.yaml      Namespace mosip-dev
│   │   │       └── configmap-iun-format.yaml  IUN 9 chiffres + Verhoeff (TOR §4.1.4)
│   │   ├── opencrvs/                   OpenCRVS (état civil + PWA hors-ligne wo/ff)
│   │   │   ├── application.yaml
│   │   │   ├── kustomization.yaml
│   │   │   ├── README.md
│   │   │   ├── values/{dev,staging,prod}.yaml
│   │   │   └── manifests/namespace.yaml
│   │   └── openimis/                   OpenIMIS (CMU/PNBSF/IPRES/CSS)
│   │       ├── application.yaml        ⚠ placeholder — pas de Helm officiel upstream
│   │       ├── kustomization.yaml
│   │       ├── README.md
│   │       ├── values/{dev,staging,prod}.yaml
│   │       └── manifests/namespace.yaml
│   │
│   └── integration/                    Couche d'intégration (à venir)
│       └── README.md                   3scale + FastAPI/Camel K adapters
│                                       (NINA, IPRES, CSS, CMU, DGI, Orange, Wave)
│
├── environments/{dev,staging,prod}/kustomization.yaml
│     (chaque overlay agrège apps/dpg/mosip + apps/dpg/opencrvs + apps/dpg/openimis)
│
├── archive/                            Pré-pivot DPG (conservé pour traçabilité)
│   ├── README.md                       Contexte de l'archivage
│   ├── CHANTIERS-OBSOLETES.md          PoC HMAC, salt migration, ADR-001 partiel, etc.
│   ├── iun/                            apps/iun/ d'origine (Quarkus skeleton)
│   ├── iun-api/                        apps/iun-api/ legacy
│   └── components/                     components/{iun-api,cnpg,keycloak,kafka}/base/
│
├── Bootstrap-IUN.ps1     Amorce l'instance Argo CD dédiée
├── Get-ArgoCD-Admin.ps1  Récupère URL UI + mot de passe admin
├── Test-IUNCluster.ps1   Précheck cluster (read-only)
├── IunOc.psm1            Module wrapper PowerShell pour `oc` (Invoke-Oc, Get-OcJson, Test-OcAccess)
├── Test-OcWrapper.ps1    Tests du module wrapper
├── .gitignore
└── README.md             (ce fichier)
```

### Pattern de déploiement (stack DPG)

```
   bootstrap/04-root-application.yaml  (Root Application "iun-root-<env>")
              |   namespace: iun-gitops   (instance dédiée)
              v   source.path = environments/<env>
   environments/<env>/kustomization.yaml
              |
              v   resources += apps/dpg/{mosip,opencrvs,openimis}
   apps/dpg/<dpg>/kustomization.yaml
              |
              v   ressource : application.yaml
   apps/dpg/<dpg>/application.yaml      (Argo CD Application)
              |
              v   source.repoURL → upstream (mosip-helm, opencrvs/infrastructure, ...)
              |   destination.namespace → <dpg>-<env>
   <namespace cible>                     pods + services + routes + PV
```

---

## 2. Composants IUN — feuille de route

### Phase courante (T+0) — bootstrap des 3 DPG

| Composant | Application Argo CD | Path Helm/Kustomize upstream | Statut |
|---|---|---|---|
| MOSIP — probe | `mosip-bootstrap-probe` | `mosip-monitoring` chart, repo `mosip-helm` | Premier sync à valider |
| OpenCRVS — core | `opencrvs-core` | Path Helm dans `opencrvs/infrastructure` | À évaluer chemin exact |
| OpenIMIS — core | `openimis-core` | Path à créer (kompose convert) | Placeholder — décision A/B/C requise |

### Phase 2 (T+1 mois) — modules applicatifs MOSIP

- `mosip-kernel-config` + `mosip-kernel-uin-generator` + `mosip-kernel-cryptomanager`
- `mosip-id-repository` + `mosip-id-authentication`
- `mosip-registration-processor` + `mosip-pre-registration`

### Phase 3 (T+2/3 mois) — intégration et front-office

- `opencrvs-countryconfig-sn` (forker / créer)
- Adapter NINA / IPRES / CSS / CMU dans `apps/integration/`
- 3scale Gateway (Operator OCP ou SaaS)
- Resident Services + Partner Management MOSIP

---

## 3. Commandes PowerShell

> **Pré-requis Windows** : `git`, `oc` 4.16+, kubeconfig pointant sur `https://api.origins.heritage.africa:6443`, module `IunOc.psm1` importé.
>
> **⚠ Certificat API expiré sur ce cluster.** Toutes les commandes `oc` ci-dessous incluent `--insecure-skip-tls-verify=true`. Alternative : faire `oc login --insecure-skip-tls-verify=true …` une fois — la session courante mémorise le flag (`~/.kube/config`). Renouvellement du cert API tracé en §5 "Hardening post-PoC".
>
> **⚠ Quoting PS 5.1** : ne JAMAIS faire `& oc ... @flags` directement, passer par `Invoke-Oc` / `Get-OcJson` / `Test-OcAccess` du module `IunOc.psm1` (cf. mémoire `[[powershell-oc-quoting]]`).

### (a) Préchecks (état cluster partagé)

```powershell
Set-Location C:\IUN_APP\gitops
.\Test-IUNCluster.ps1   # rapport markdown + .logs\precheck-YYYYMMDD-HHMMSS.md
```

Sortie attendue : OpenShift GitOps + 8 autres Operators socle remontent en `OK`, Service Mesh 3 et RHACS en `INFO` (non installés).

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

### (d) Vérifier la synchronisation des 3 DPG

```powershell
Import-Module .\IunOc.psm1

# Liste des Applications DPG dans iun-gitops
Invoke-Oc get applications -n iun-gitops

# Vue d'ensemble santé
Invoke-Oc get applications -n iun-gitops `
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status

# Forcer un refresh / sync manuel d'une Application
Invoke-Oc annotate application mosip-bootstrap-probe -n iun-gitops `
  argocd.argoproj.io/refresh=hard --overwrite

# Statut détaillé d'un DPG
Get-OcJson get application opencrvs-core -n iun-gitops |
  Select-Object -ExpandProperty status |
  Select-Object sync, health
```

### (e) Promouvoir vers staging / prod

```powershell
.\Bootstrap-IUN.ps1 -Environment staging
# ou
.\Bootstrap-IUN.ps1 -Environment prod
```

### (f) Rollback complet de l'instance IUN

```powershell
Import-Module .\IunOc.psm1
Invoke-Oc delete application iun-root-dev -n iun-gitops
Invoke-Oc delete argocd iun-argocd        -n iun-gitops
Invoke-Oc delete namespace iun-gitops
# Les namespaces DPG (mosip-dev, opencrvs-dev, openimis-dev) sont nettoyés
# par le finalizer Argo CD AVANT la suppression de l'instance.
```

---

## 4. Conventions

- **Un seul AppProject** (`iun-platform`) à ce stade ; segmenter par DPG ou par sécurité quand l'équipe scalera (`iun-mosip`, `iun-opencrvs`, `iun-openimis`).
- **Pas de Secrets en clair dans Git** : tout passe par External Secrets Operator + Vault (les deux déjà installés cluster), ou Sealed Secrets en transition.
- **Toutes les Applications Argo CD** vivent dans `iun-gitops` (`metadata.namespace`). Leurs `destination.namespace` restent les namespaces métier cibles (`mosip-dev`, `opencrvs-dev`, `openimis-dev`).
- **Aucune Subscription Operator dans ce repo** — un PR qui en ajoute une doit être refusé (cohérence cluster partagé).
- **selfHeal=false sur les Applications DPG** au démarrage, tant qu'on valide manuellement chaque sync. À évaluer pour activer en prod une fois la chaîne stabilisée.
- **`sourceRepos` du AppProject** doit lister `mosip-helm`, `opencrvs/infrastructure`, et le chemin OpenIMIS retenu — voir `bootstrap/03-root-app-project.yaml` (TODO post-commit).

---

## 5. Hardening post-PoC (dette technique + cible TOR)

À traiter après validation fonctionnelle des 3 DPG, avant bascule staging/prod :

| # | Item                                  | Description                                                                                                                                            | Priorité |
|---|---------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------|----------|
| 1 | Cert API serveur OCP expiré           | Renouveler le cert de `api.origins.heritage.africa:6443` pour supprimer `--insecure-skip-tls-verify=true`. Procédure `oc adm` côté SRE.                | Haute    |
| 2 | SA controller = cluster-admin         | Remplacer le CRB cluster-admin (`bootstrap/02-iun-rbac.yaml`) par un ClusterRole fine-grained `iun-argocd-platform` (limité aux CR métier des 3 DPG).  | Haute    |
| 3 | SSO Argo CD désactivé                 | Activer OIDC sur le realm Keycloak (RHBK ou Keycloak embarqué MOSIP — à arbitrer).                                                                     | Moyenne  |
| 4 | HA Argo CD désactivée                 | `spec.ha.enabled: true` sur le CR `iun-argocd` (Redis cluster, controller sharding). Coût ~3x mémoire.                                                 | Moyenne  |
| 5 | sourceRepos restreint                 | Étendre à `mosip-helm` et `opencrvs/infrastructure` ; à durcir après migration vers Git on-prem (GitLab souverain / Gitea).                            | Haute    |
| 6 | Pipeline de validation Kustomize      | Tekton job `kustomize build environments/dev \| oc apply --dry-run=server -f -` sur chaque PR.                                                         | Basse    |
| 7 | **Service Mesh 3 + mTLS bout en bout**| TOR §3.3.1. Décider de la cohabitation SM2/SM3 ou bascule. Backlog confirmé.                                                                           | Haute    |
| 8 | **3scale API Management**             | TOR §3.3.2 + §5.4. Gateway officielle. Décision on-prem (Operator OCP) vs Red Hat Cloud Services à arbitrer.                                           | Haute    |
| 9 | **HSM Luna FIPS 140-2 L3**            | TOR §3.3.3 + §4.3.2. Intégration via Vault HSM ou direct PKCS#11 dans MOSIP keymanager.                                                                | Haute    |
| 10| **Topologie 3 sites + RHACM DR DC3**  | TOR §5.2. Stretched DC1+DC2 Diamniadio + DR DC3 Mbour DOUANES. RTO < 4h, RPO < 1h.                                                                     | Critique pour prod |
| 11| **Compliance Operator (FIPS+STIG+CIS)** | TOR §3.3.4 + §5.4 + §6.3. Validation continue de la conformité ISO 27001 visée à M24.                                                                | Haute    |
| 12| **RHACS ou alternative OSS**          | Sécurité runtime. Choix entre souscription Red Hat ou StackRox community.                                                                              | Moyenne  |

---

## 6. Pour creuser

- **Document stratégique** : `C:\IUN_APP\architecture\REPENSEE-PROJET-v1.md` — rationalé du pivot DPG, plan 30 jours, 7 décisions à arbitrer pour le 7 juin 2026.
- **TOR officielle** : `proposition_IUN_Senegal_vf.docx` v2.0 (avril 2026).
- **Rapport Red Hat OCP** : `C:\IUN_APP\EVALUATION_OPENSHIFT_v4.16.md` (toujours valide sur le socle, à relativiser sur la couche applicative).
- **Intégration systèmes existants** : `C:\IUN_APP\architecture\INTEGRATION-SYSTEMES-EXISTANTS-v1.md` — analyse du guide Accel Tech FastAPI (désormais reconnu comme couche d'intégration légère).
- **Chantiers obsolètes** : `archive/CHANTIERS-OBSOLETES.md` — ce qui a été abandonné par le pivot et pourquoi.

---

*Dernière révision : 2026-05-24 — pivot DPG acté.*
