# OpenIMIS — Insurance Management Information System

## Rôle dans IUN Sénégal

OpenIMIS gère la **protection sociale** : affiliations, cotisations, prestations
des grands régimes sociaux sénégalais. **L'IUN est la clé primaire** de toutes les
affiliations — toute personne enrôlée dans MOSIP peut ouvrir des droits dans
OpenIMIS. Référence TOR :

- **§3.2 C3** — Composant 3 : protection sociale (OpenIMIS)
- **§4.4** — Régimes couverts : CMU (santé), PNBSF (bourse), IPRES (retraite), CSS (famille)
- **§3.4.1** — Enrôlement nouveau-né déclenche ouverture droits CMU automatique
- **§4.4** — Sync hebdomadaire IPRES, temps réel CSS, quotidienne CMU/MSAS
- **§4.4** — Ordres de virement Trésor Public pour PNBSF, paiements mobiles Wave + Orange Money

## ⚠ Source officielle — situation atypique

**OpenIMIS n'expose pas de Helm chart officiel à ce jour.** Le déploiement
canonique upstream est un **docker-compose all-in-one** (`openimis-dist_dkr`).

| Élément | Valeur | Notes |
|---|---|---|
| Distribution all-in-one | `https://github.com/openimis/openimis-dist_dkr` | **docker-compose**, pas K8s |
| Backend Python (Django) | `https://github.com/openimis/openimis-be_py` | API REST + GraphQL |
| Frontend JS (React) | `https://github.com/openimis/openimis-fe_js` | UI modulaire |
| Helm chart officiel | **❌ N'EXISTE PAS** | Voir options ci-dessous |
| Doc | https://openimis.atlassian.net/wiki/ | Maintainer Guide |
| Communauté | https://openimis.org/our-tools | Canal officiel |

### Trois options pour Kubernétiser OpenIMIS

| Option | Description | Effort | Risque |
|---|---|---|---|
| **A — Kompose convert** | Adapter le docker-compose officiel via `kompose convert` puis affiner Kustomize. Tracking explicite des écarts. | Moyen | Maintenance manuelle à chaque release upstream |
| **B — Fork communautaire** | Chercher des forks "early-stage" Helm OpenIMIS sur GitHub (qq existent) et auditer leur maturité. | Faible si fork fiable | Risque d'orphelinat / divergence upstream |
| **C — Initiative upstream** | Contacter la communauté OpenIMIS (canal #deployment, Atlassian wiki) pour savoir si un effort Helm est en cours et y contribuer. | Élevé si on devient mainteneur | Calendrier hors notre contrôle |

**Recommandation T+1** : commencer par **(B)** (audit forks) en parallèle de **(C)**
(contact upstream), avec **(A)** en plan B garanti. Décision à acter sous 2 semaines.

## Composants déployés

Composants OpenIMIS attendus dans le namespace `openimis-dev` :

| Composant | Image upstream | Rôle |
|---|---|---|
| `openimis-be` | `openimis/openimis-be_py` | API Django backend |
| `openimis-fe` | `openimis/openimis-fe_js` | UI React |
| `openimis-db` | `postgres:13` ou CNPG cluster externe | Persistence métier |
| `openimis-worker` | `openimis/openimis-worker` (à confirmer) | Tâches asynchrones (paiements, sync) |
| `opensearch` | `opensearchproject/opensearch` | Recherche / dashboards |
| `rabbitmq` | `rabbitmq:3` | Bus de messages (workflow) |
| `lightning` | `bitnami/openimis-lightning` (à confirmer) | Module paiements |

## Personnalisations Sénégal

Dans `values/dev.yaml` (logique — pas un vrai chart Helm aujourd'hui) :

- **`products`** : 4 régimes CMU / PNBSF / IPRES / CSS modélisés dès l'amorce
- **`integrations.mosip`** : webhook à activer dès que MOSIP kernel est up — IUN devient clé primaire des `Insuree`
- **`integrations.opencrvs`** : enrôlement automatique CMU à la naissance (TOR §3.4.1)
- **`integrations.externalSystems`** : sync IPRES (hebdo), CSS (temps réel), CMU (quotidien)
- **`mobilePayments.{orangeMoney,wave}`** : à activer en phase pilote (TOR §4.4)
- **`treasury`** : connecteur Trésor Public pour ordres de virement PNBSF (TOR §4.4)

## Prérequis cluster

| Ressource | Dev | Staging | Prod (target M24) |
|---|---|---|---|
| vCPU réservés | ~15 | ~50 | ~150 |
| RAM réservée | ~30 Gi | ~100 Gi | ~300 Gi |
| Stockage ODF | ~50 Gi | ~300 Gi | ~3 Ti |
| Operators OCP | CNPG, ODF, Logging, GitOps | + AMQ Streams (notif Kafka) | + 3scale + RHACM |

## Procédure de déploiement (commandes PowerShell)

```powershell
# ⚠ Tant que les manifests K8s OpenIMIS ne sont pas produits, l'Application
# Argo CD `openimis-core` restera en `OutOfSync` / `Missing` car son `path`
# n'existe pas encore (apps/dpg/openimis/manifests-kompose-tbd).
# C'est volontaire — voir le backlog ci-dessous.

Set-Location C:\IUN_APP\gitops
.\Test-IUNCluster.ps1

Import-Module .\IunOc.psm1
Invoke-Oc annotate application openimis-core -n iun-gitops `
  argocd.argoproj.io/refresh=hard --overwrite

Get-OcJson get application openimis-core -n iun-gitops |
  Select-Object -ExpandProperty status |
  Select-Object sync, health
```

## Validation post-déploiement (cible — pas applicable tant que les manifests ne sont pas livrés)

- Application Argo CD `openimis-core` en `Synced` / `Healthy`
- Pods `openimis-be`, `openimis-fe`, `openimis-db` en `Running`
- Route exposée : `https://openimis-dev-iun.apps.origins.heritage.africa`
- Login admin OpenIMIS opérationnel
- (Phase 2) Création test : créer une famille fictive, l'affilier à CMU, vérifier que l'IUN MOSIP est bien la clé.

## Backlog / TODO

- **Décider de l'option (A) / (B) / (C)** pour Kubernétiser OpenIMIS. T+2 semaines max.
- **Si (A)** : faire `kompose convert` sur `openimis-dist_dkr` @ latest release, produire les manifests dans `manifests-kompose/`, ajuster Application Argo CD.
- **Modéliser les 4 régimes** dans OpenIMIS : créer les `Product` CMU / PNBSF / IPRES / CSS via les outils admin OpenIMIS.
- **Webhook MOSIP → OpenIMIS** : à l'enrôlement IUN, créer un `Insuree` OpenIMIS avec IUN comme clé.
- **Webhook OpenCRVS → OpenIMIS** : à l'enregistrement de naissance, ouvrir droits CMU.
- **Connecteurs externes** : IPRES (sync hebdo), CSS (temps réel via 3scale), CMU/MSAS (quotidien).
- **Paiements mobiles** : intégration Wave et Orange Money pour PNBSF + cotisations CMU.
- **Connecteur Trésor Public** : ordres de virement PNBSF — à co-définir avec Direction Trésor.
- **Auth harmonisée RHBK** : remplacer auth interne OpenIMIS par RHBK realm IUN.

## Références

- TOR officielle v2.0 : `proposition_IUN_Senegal_vf.docx`
- Document stratégique de pivot : `C:\IUN_APP\architecture\REPENSEE-PROJET-v1.md`
- Doc OpenIMIS : https://openimis.org/our-tools
- Maintainer Guide : https://openimis.atlassian.net/wiki/spaces/OP/pages/4468768808/Maintainer+Guide
- Distribution Docker : https://github.com/openimis/openimis-dist_dkr
