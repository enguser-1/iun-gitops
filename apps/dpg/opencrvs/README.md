# OpenCRVS — Open Civil Registration & Vital Statistics

## Rôle dans IUN Sénégal

OpenCRVS gère **l'état civil** : enregistrement des naissances, décès, mariages,
divorces, légalisations. C'est le **point d'entrée** du citoyen dans le système
identitaire — la déclaration de naissance déclenche l'enrôlement MOSIP qui
génère l'IUN. Référence TOR officielle v2.0 :

- **§3.2 C1** — Composant 1 : état civil (OpenCRVS, FHIR R4 / Hearth)
- **§3.4.1** — Flow nouveau-né maternité → OpenCRVS → MOSIP → OpenIMIS (CMU)
- **§4.2** — PWA React mode hors-ligne, 14 régions / 46 départements,
  langues officielle (français) + nationales (wolof / pulaar), recherche
  phonétique, déclaration tardive code de la famille art. 54
- **§4.12 / §3.4.2** — NIA (Numéro d'Identification d'Attente) si déclaration
  tardive en attente de validation tribunal d'instance

## Source officielle (vérifiée 2026-05-24)

| Élément | Valeur | Notes |
|---|---|---|
| Repo microservices | `github.com/opencrvs/opencrvs-core` | Code, images Docker |
| Repo Helm charts | `github.com/opencrvs/opencrvs-helm-charts` | **Archivé 2026-05-20** — develop HEAD frozen, merged dans opencrvs-core (cf. CHANGELOG) |
| Template country config | `github.com/opencrvs/opencrvs-countryconfig` | À forker — voir `countryconfig-sn-template/` |
| **Version core retenue** | **v1.9.13** | Release stable la plus récente, **2026-05-08** |
| Version chart | `develop` (frozen) | À pinner à un commit SHA explicite au 1er déploiement |
| Documentation | <https://documentation.opencrvs.org/> | Référence opérationnelle |

> **Règle upstream** : `infrastructure`/`helm-charts` et `opencrvs-core` doivent
> être sur la **même version**. Comme le repo helm-charts est figé, ses dernières
> templates ciblent les images core jusqu'à v1.9.13 inclus.

## Topologie MVP

```
                       ┌─────────────────────────┐
                       │  OCP Route (iter 2)     │
                       │  opencrvs-dev.apps…     │
                       └────────────┬────────────┘
                                    │
                       ┌────────────▼────────────┐
                       │ client (PWA React)      │
                       │ gateway (GraphQL)       │
                       └────────────┬────────────┘
                                    │
        ┌───────┬───────┬───────────┼───────────┬───────┬───────────┐
   ┌────▼───┐ ┌─▼────┐ ┌▼────────┐ ┌▼────────┐ ┌▼─────┐ ┌▼────────┐
   │ auth   │ │ user │ │ workflow│ │ search  │ │hearth│ │webhooks │
   │        │ │ mgnt │ │         │ │         │ │FHIR  │ │ → MOSIP │
   └────────┘ └──────┘ └─────────┘ └─────────┘ └──┬───┘ └─────────┘
                                                    │
   ┌────────────────┬────────────┬─────────────┬────┴────┬──────────┐
   │                │            │             │         │          │
┌──▼──┐         ┌───▼──┐    ┌────▼───┐    ┌────▼───┐ ┌───▼───┐ ┌───▼────┐
│Mongo│         │  ES  │    │ Redis  │    │ MinIO  │ │Influx │ │Postgres│
│4.4  │         │ 8.19 │    │        │    │(→ OBC) │ │       │ │ (FDW)  │
└─────┘         └──────┘    └────────┘    └────────┘ └───────┘ └────────┘
        ↑                                       ↑
        │                                       │
   ODF Ceph RBD                            ODF Ceph RGW (iter 2)
   (PVC pour stateful)                     (ObjectBucketClaim)
```

## Composants OpenCRVS (microservices, chart `opencrvs-services`)

| Composant | Rôle |
|---|---|
| `client` | PWA React (citoyen + agent enregistreur, mode hors-ligne IndexedDB) |
| `gateway` | API gateway interne (GraphQL agrégant les services) |
| `auth` | Authentification (JWT + RHBK ou Keycloak interne) |
| `user-mgnt` | Gestion utilisateurs/rôles |
| `notification` | SMS / email / push |
| `metrics` | Statistiques publiques sur enregistrements |
| `webhooks` | Hooks externes (MOSIP UIN Generator, OpenIMIS) |
| `workflow` | Orchestration états de l'enregistrement (DECLARED → REGISTERED) |
| `search` | Elasticsearch indexer + recherche phonétique |
| `documents` | Stockage / preview des pièces justificatives |
| `hearth` | FHIR R4 server (persistence MongoDB) |
| `config` | Configuration pays (loaded depuis countryconfig fork) |

## Dépendances stack (chart `dependencies`)

| Composant | Version pinnée | Rôle |
|---|---|---|
| MongoDB | 4.4 | Persistence FHIR (Hearth), users, config |
| Elasticsearch | 8.19.15 | Index search + recherche phonétique |
| Redis | (Bitnami latest) | Cache session + rate limiting |
| MinIO | (latest) | Stockage objet (PJ scannées) — remplacé par OBC ODF en iter 2 |
| InfluxDB | (latest) | Métriques agrégées |
| Postgres | 17.6 + mongo_fdw | Reporting / Metabase |

## Personnalisations Sénégal (values/dev.yaml)

Différences vs défauts Farajaland :

- **`global.country: SN`** + **`global.locale.primary: fr`** +
  **`global.locale.secondary: [wo, ff]`**
- **`global.countryConfig.repoURL`** : placeholder
  `github.com/opencrvs/opencrvs-countryconfig` — **à pointer sur
  `github.com/enguser-1/opencrvs-countryconfig-sn`** après création du fork.
  Procédure détaillée : `countryconfig-sn-template/README.md`.
- **`ingress.ssl_enabled: false`** : SSL géré par OCP Router (re-encrypt iter 2)
- **`minio.enabled: true`** : MVP. Iter 2 → OBC + `minio.enabled: false`
- **`integrations.mosip.enabled: false`** : activé P2 quand MOSIP Healthy

## Prérequis cluster

| Ressource | Dev MVP | Notes |
|---|---|---|
| vCPU réservés | ~8 | Limite cumulée dev sandbox |
| RAM réservée | ~16 Gi | Mongo + ES + microservices |
| Stockage ODF (RBD) | ~30 Gi | Mongo 10Gi + ES 15Gi + Influx 5Gi |
| Stockage ODF (RGW) | — | MVP : MinIO local. Iter 2 : OBC RGW |
| Operators OCP | GitOps, ODF, Logging, cert-manager | Approche B — cf. `[[project-iun-approach-b]]` |
| `vm.max_map_count` node | 262144 | **À vérifier** — requis pour Elasticsearch 8.x. Si absent, cluster-admin doit créer MachineConfig via TuningOperator |
| StorageClass `ocs-storagecluster-ceph-rgw` | Doit exister | Vérifier via `oc get sc \| grep rgw` |

## Procédure de bootstrap (PowerShell)

> Cluster API cert expiré → `--insecure-skip-tls-verify=true` géré par les
> helpers de `IunOc.psm1` (cf. `[[powershell-oc-quoting]]`).

```powershell
Set-Location C:\IUN_APP\gitops
Import-Module .\IunOc.psm1

# 1) Précheck cluster (Operators, RBAC, StorageClass ODF, vm.max_map_count)
.\Test-IUNCluster.ps1

# 2) Appliquer les pré-requis manifest (namespace + SCC + OBC)
Invoke-Oc apply -f apps/dpg/opencrvs/manifests/namespace.yaml
Invoke-Oc apply -f apps/dpg/opencrvs/manifests/scc-opencrvs.yaml
# OBC créée en iter 2 — décommenter quand `minio.enabled` passera à false
# Invoke-Oc apply -f apps/dpg/opencrvs/manifests/objectbucketclaim.yaml

# 3) Apply les 2 Applications Argo CD (deps + services)
Invoke-Oc apply -f apps/dpg/opencrvs/application.yaml

# 4) Forcer un refresh (les automated.prune=false impliquent sync manuel)
Invoke-Oc annotate application opencrvs-deps -n iun-gitops `
  argocd.argoproj.io/refresh=hard --overwrite
Invoke-Oc annotate application opencrvs-services -n iun-gitops `
  argocd.argoproj.io/refresh=hard --overwrite

# 5) Statut
Get-OcJson get application opencrvs-deps -n iun-gitops |
  Select-Object -ExpandProperty status |
  Select-Object sync, health
Get-OcJson get application opencrvs-services -n iun-gitops |
  Select-Object -ExpandProperty status |
  Select-Object sync, health

# 6) Pods OpenCRVS
Invoke-Oc get pods -n opencrvs-dev
```

## Validation post-déploiement

### Phase MVP (cette itération)

1. Les 2 Applications Argo CD en `Synced` / `Healthy`
2. Pods Healthy :
   - `mongodb-0`, `elasticsearch-0`, `minio-0`, `redis-0`, `influxdb-0`,
     `postgres-0` (chart deps)
   - `client`, `gateway`, `auth`, `hearth`, `search`, `workflow`, `user-mgnt`,
     `documents`, `notification`, `metrics`, `webhooks`, `config` (chart services)
3. Port-forward UI client : `oc port-forward -n opencrvs-dev svc/client 8080:80`
   → <http://localhost:8080> doit afficher la page de login Farajaland
4. Création d'un compte test via seed Farajaland :
   `oc exec -n opencrvs-dev deploy/user-mgnt -- node /app/scripts/seed-users.js`

### Phase iter 2 (intégration MOSIP/IUN)

5. Création d'une naissance test → vérifier création FHIR Patient dans Hearth :
   ```powershell
   Invoke-Oc exec -n opencrvs-dev deploy/hearth -- curl -s `
     http://localhost:3447/fhir/Patient | jq '.entry[0].resource'
   ```
6. Vérifier webhook MOSIP appelé (logs `webhooks` service)
7. Vérifier IUN persisté dans `Patient.identifier[system='https://anie.sn/iun']`

## Backlog itération 2

- **Forker `opencrvs-countryconfig-sn`** depuis le template — procédure complète
  dans `countryconfig-sn-template/README.md`
- **Mettre à jour** `values/dev.yaml` → `global.countryConfig.repoURL`
- **Activer OBC** ODF + désactiver MinIO embarqué (cf. `manifests/objectbucketclaim.yaml`)
- **Créer route OCP** `route-opencrvs-client.yaml` avec re-encrypt termination
- **Recherche phonétique wolof** : analyzer Elasticsearch custom (collaboration UCAD)
- **Intégration MOSIP** : webhook `webhooks` → MOSIP UIN Generator (TOR §3.4.1)
- **Auth harmonisée RHBK** : remplacer auth interne par realm IUN
- **Sauvegarde MongoDB** : politique de backup compatible RPO < 1h (TOR §5.2)
- **SCC restricted-v2** strict : ajouter `securityContext.runAsUser` partout dans values
- **Mode offline + biométrie sur tablette Android** : intégration avec MOSIP registration-client
- **Service Mesh** : SMMR opencrvs-dev + `istio-injection: enabled`

## Risques connus

1. **API du chart non figée** — `opencrvs-helm-charts` est archivé sans tag
   semver. La référence `develop` peut bouger si quelqu'un fait `git push -f`
   (très improbable sur un archive). **Mitigation** : pinner SHA au 1er sync.
2. **Helm `valueFiles` cross-repo** — Argo CD doit être en mode `multiple
   sources` (>= 2.7). Si l'instance `iun-argocd` ne l'a pas, plan B : inliner
   les values dans `application.yaml` via `helm.values: |`.
3. **Traefik IngressRoute** — le chart deps embarque une CRD Traefik que OCP
   ne gère pas nativement. **Mitigation** : `ingress.ssl_enabled: false` +
   exposition via OCP Route (à créer iter 2).
4. **`vm.max_map_count`** — Elasticsearch 8.x refuse de démarrer si < 262144.
   **Mitigation** : prérequis cluster, à valider via TuningOperator.

## Références

- TOR officielle v2.0 : `proposition_IUN_Senegal_vf.docx`
- Document stratégique : `C:\IUN_APP\architecture\REPENSEE-PROJET-v1.md`
- Documentation OpenCRVS : <https://documentation.opencrvs.org/>
- Repo core (release notes) : <https://github.com/opencrvs/opencrvs-core/releases>
- Repo charts (archivé) : <https://github.com/opencrvs/opencrvs-helm-charts>
- Template country config : <https://github.com/opencrvs/opencrvs-countryconfig>
- Notes pays SN — codes administratifs : `countryconfig-sn-template/hierarchy-skeleton.csv`
