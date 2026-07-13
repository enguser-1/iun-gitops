# iun-civil-registry — GitOps manifests

Namespace cible : `iun-opencrvs-dev` (OCP 4.16).

## Pourquoi un wrapper custom

Sprint 2 Week 4 — pivot après ~6 h sur OpenCRVS 1.9.13 officiel (Events V2, migrations Postgres
manquantes, collisions env K8s). On reprend exactement le pattern qui a marché pour
`iun-uin-service` : FastAPI custom + BuildConfig OCP + Mongo existant. Le vrai OpenCRVS sera
repris au Sprint 3 avec un kit révisé en lisant le code source d'abord.

## Composants livrés

| Manifest | Objet | Notes |
|----------|-------|-------|
| `01-buildconfig.yaml` | ImageStream + BuildConfig (Binary, Docker strategy) | build on-cluster, source via `oc start-build --from-dir` |
| `02-deployment.yaml` | Deployment (2 replicas) + ConfigMap | trigger ImageStream auto. ConfigMap = endpoints (Mongo, uin-service) |
| `03-service.yaml` | Service ClusterIP 8080 | name = `iun-civil-registry` |
| `04-route.yaml` | Route OCP edge TLS | `https://iun-civil-dev.apps.origins.heritage.africa` |
| `05-network-policy-mosip-egress.yaml` | NP egress cross-NS + builds HTTPS | DNS 5353 + iun-mosip-dev/iun-uin-service:8080 + builds vers WAN/registry |

## Chaîne d'appels

```
client                Route (edge TLS)
  │                     │
  ▼                     ▼
HTTPS  ─►  iun-civil-registry (POST /v1/civil/birth)
                  │
                  ├─► MongoDB (mongo:27017 / db civil_registry_iun)
                  │       INSERT birth_registrations
                  │       findOneAndUpdate brn_counters
                  │
                  └─► iun-uin-service.iun-mosip-dev.svc.cluster.local:8080
                          POST /v1/idgenerator/uin → UIN 10 chiffres + Verhoeff
```

## Secret consommé

`mongodb-creds` (déjà géré par `01-secrets.ps1` Week 4 OpenCRVS) :

- clé `app-username` → env `MONGO_USER`
- clé `app-password` → env `MONGO_PASSWORD`

Le user `app` doit avoir les droits read/write sur la DB `civil_registry_iun`. Si ce n'est pas
le cas, voir le bloc *Provisionner la DB Mongo* du README global du kit.

## Ordre d'apply (séquence shell équivalente)

```bash
oc apply -k . --server-side --field-manager=iun-civil-registry \
   --insecure-skip-tls-verify=true
oc start-build iun-civil-registry --from-dir=C:\IUN_APP\sprint-2-scripts\week-4-civil-registry\src \
   -n iun-opencrvs-dev --follow --insecure-skip-tls-verify=true
oc rollout status deploy/iun-civil-registry -n iun-opencrvs-dev --timeout=240s \
   --insecure-skip-tls-verify=true
```

…ou, plus simple côté Lead :

```powershell
cd C:\IUN_APP\sprint-2-scripts\week-4-civil-registry
.\Run-CivilRegistry.ps1
```
