# OpenCRVS — Open Civil Registration & Vital Statistics

## Rôle dans IUN Sénégal

OpenCRVS gère **l'état civil** : enregistrement des naissances, décès, mariages.
C'est le **point d'entrée** du citoyen dans le système identitaire — la déclaration de
naissance déclenche l'enrôlement MOSIP qui génère l'IUN. Référence TOR :

- **§3.2 C1** — Composant 1 : enregistrement état civil (OpenCRVS)
- **§3.4.1** — Enrôlement nouveau-né depuis maternité → OpenCRVS → MOSIP → OpenIMIS automatique (ouverture droits CMU)
- **§4.2** — PWA React mode hors-ligne (IndexedDB + sync CRDT), 46 départements, wolof/pulaar, recherche phonétique Elasticsearch, déclaration tardive code de la famille art. 54
- **§4.12 / §3.4.2** — NIA (Numéro d'Identification d'Attente) si déclaration tardive en attente de validation tribunal

## Source officielle

| Élément | Valeur | Notes |
|---|---|---|
| Repo principal | `https://github.com/opencrvs/opencrvs-core` | Microservices applicatifs |
| Repo infrastructure | `https://github.com/opencrvs/infrastructure` | **Helm charts** officiels |
| Version visée | **v1.9.12** (latest stable, série 1.9) | Bug fixes + improvements en cours |
| Doc | https://documentation.opencrvs.org/ | Référence opérationnelle |
| Country config | repo séparé `opencrvs-countryconfig` par pays | À forker pour le Sénégal — TBD |

> **Règle upstream** : garder `infrastructure` et `opencrvs-core` **sur la même version**.

## Composants déployés (microservices)

OpenCRVS est un ensemble de microservices Node.js + une PWA React. Composants principaux :

| Composant | Rôle |
|---|---|
| `client` | PWA React (interface citoyen + agent enregistreur) |
| `gateway` | API gateway interne (GraphQL) |
| `auth` | Authentification (JWT + RHBK ou Keycloak interne) |
| `user-mgnt` | Gestion utilisateurs/rôles |
| `notification` | SMS/email/push aux citoyens |
| `metrics` | Stats publiques sur enregistrements |
| `webhooks` | Hooks vers systèmes externes (MOSIP, OpenIMIS) |
| `workflow` | Orchestration des étapes d'enregistrement |
| `search` | Elasticsearch indexer (phonétique wolof/français) |
| `documents` | OCR + stockage MinIO des PJ |
| `hearth` | FHIR R4 server (persistence MongoDB) |
| `config` | Configuration country (46 départements, formulaires, etc.) |

## Personnalisations Sénégal

Différences vs valeurs par défaut, documentées dans `values/dev.yaml` :

- **`country: SN`** + **`locale.primary: fr`** + **`locale.secondary: [wo, ff]`** (wolof, pulaar)
- **`countryConfig.repoURL`** : à pointer sur un fork `opencrvs-countryconfig-sn` à créer (46 départements, formulaires naissance/décès/mariage adaptés au code de la famille)
- **`declarations.lateRegistration`** : workflow art. 54 (témoins + tribunal + NIA provisoire)
- **`search.phoneticAlgorithm`** : doubleMetaphone, à challenger pour le wolof (custom analyzer ES probable)
- **`integrations.mosip.enabled`** : faux en dev, vrai dès que kernel MOSIP est up (webhook → UIN generator)
- **`ingress.enabled: false`** : on expose via OpenShift Route

## Prérequis cluster

| Ressource | Dev | Staging | Prod (target M24) |
|---|---|---|---|
| vCPU réservés | ~20 | ~60 | ~200 |
| RAM réservée | ~40 Gi | ~120 Gi | ~400 Gi |
| Stockage ODF | ~100 Gi (mongo + ES + MinIO) | ~500 Gi | ~5 Ti |
| Operators OCP | ODF, Logging, cert-manager, GitOps | + Service Mesh | + 3scale + RHACM |

> **MongoDB embarqué** : OpenCRVS embarque MongoDB par défaut. À évaluer s'il vaut mieux
> le faire fournir par un Operator dédié (Percona MongoDB Operator, par ex.) ou consolider
> dans CNPG (CNPG ne fait que Postgres — pas applicable). Backlog.

## Procédure de déploiement (commandes PowerShell)

```powershell
Set-Location C:\IUN_APP\gitops

# 1. Précheck cluster
.\Test-IUNCluster.ps1

# 2. Refresh + sync Application OpenCRVS
Import-Module .\IunOc.psm1
Invoke-Oc annotate application opencrvs-core -n iun-gitops `
  argocd.argoproj.io/refresh=hard --overwrite

# 3. Statut
Get-OcJson get application opencrvs-core -n iun-gitops |
  Select-Object -ExpandProperty status |
  Select-Object sync, health

# 4. Pods OpenCRVS
Invoke-Oc get pods -n opencrvs-dev
```

## Validation post-déploiement

- Application Argo CD `opencrvs-core` en `Synced` / `Healthy`
- Pods `client`, `gateway`, `auth`, `hearth`, `search`, `workflow` en `Running`
- Route exposée : `https://opencrvs-dev-iun.apps.origins.heritage.africa`
- Health endpoint : `GET /ping` → 200
- (Phase 2) Création test : enregistrer une naissance fictive, vérifier création FHIR Patient + webhook MOSIP appelé.

## Backlog / TODO

- **Forker / créer `opencrvs-countryconfig-sn`** : 46 départements, formulaires naissance/décès/mariage adaptés au code de la famille sénégalais, mapping FHIR.
- **Recherche phonétique wolof** : valider le `doubleMetaphone` ou créer un analyzer Elasticsearch custom — collaboration avec un linguiste.
- **PWA hors-ligne sur tablettes ANTI-fraude** : tester le sync CRDT sur connexion fibre + 4G + sync nocturne VSAT (TOR §5.6.2 unités mobiles).
- **Intégration MOSIP** : implémenter webhook OpenCRVS → MOSIP UIN Generator, et notification Kafka aux systèmes tiers.
- **Auth harmonisée RHBK** : remplacer auth interne par RHBK realm IUN (réutiliser realm créé par MOSIP ou en créer un transverse).
- **Mode offline + biométrie sur tablette Android** : interaction avec le module registration-client de MOSIP (qui couvre ce périmètre côté MOSIP).
- **Sauvegarde MongoDB** : politique de backup compatible RPO < 1h (TOR §5.2).

## Références

- TOR officielle v2.0 : `proposition_IUN_Senegal_vf.docx`
- Document stratégique de pivot : `C:\IUN_APP\architecture\REPENSEE-PROJET-v1.md`
- Doc OpenCRVS : https://documentation.opencrvs.org/
- Helm charts OpenCRVS : https://github.com/opencrvs/infrastructure
- Release notes v1.9.x : https://github.com/opencrvs/opencrvs-core/releases
