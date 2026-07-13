# iun-uin-bridge Helm Chart

Chart Helm pour déployer le pont IUN Sénégal entre OpenCRVS et iun-uin-service (IDgenerator MOSIP-compatible).

## Vue d'ensemble

Le bridge :

- Poll toutes les 30s les `Task REGISTERED` dans Hearth (Mongo)
- Détecte l'événement (birth via section `child-details`, death via `deceased-details`)
- Mint un UIN Verhoeff 10-digits via iun-uin-service, formate en `SN-XXXX-XXXX-XX`
- Génère un BRN régional `RRR-YYYY-NNNNNN` (birth) ou DRN `RRR-YYYY-DNNNNNN` (death) via compteur atomique Mongo
- PUT sur le Patient FHIR (UIN + national-id + BRN/DRN)
- Expose `/records` (registre HTML), `/certificate/{event}/{patient_id}` (SVG rendu), `/status` (métriques)

## Installation

```bash
# Ajouter le chart en local (ou depuis un repo Helm plus tard)
cd C:\IUN_APP\cowork\helm\iun-uin-bridge

# Prérequis : Secret contenant MONGO_URL
kubectl -n iun-opencrvs-dev create secret generic mongo-hearth-uri \
  --from-literal=MONGO_URL='mongodb://app:XXXXXX@mongo-headless:27017/hearth-dev?authSource=user-mgnt'

# Dev
helm install iun-uin-bridge . -n iun-opencrvs-dev -f values-dev.yaml

# Prod (après build image + digest)
helm install iun-uin-bridge . -n iun-opencrvs-prod \
  -f values-prod.yaml \
  --set image.digest=sha256:XXXXXX
```

## Build image (BuildConfig OpenShift)

```bash
# Le chart déploie aussi BuildConfig + ImageStream si build.enabled=true
oc -n iun-opencrvs-dev start-build iun-uin-bridge --from-dir=./bridge/ --follow

# Récupérer le digest push et pinner
DIGEST=$(oc -n iun-opencrvs-dev get is iun-uin-bridge -o jsonpath='{.status.tags[?(@.tag=="latest")].items[0].image}')
helm upgrade iun-uin-bridge . -n iun-opencrvs-dev --set image.digest=$DIGEST
```

## Configuration

Voir `values.yaml` pour tous les paramètres. Les plus importants :

| Clé | Description | Défaut |
|---|---|---|
| `image.digest` | Digest SHA256 de l'image (obligatoire en prod) | vide |
| `env.DRY_RUN` | Si `true`, ne mute pas Hearth (mode observation) | `"false"` |
| `env.POLL_INTERVAL_S` | Fréquence de poll en secondes | `"30"` |
| `env.HEARTH_URL` | Endpoint Hearth interne | `http://hearth:3447` |
| `env.UIN_SERVICE_URL` | Endpoint iun-uin-service | `http://iun-uin-service.iun-mosip-dev.svc.cluster.local:8080` |
| `mongo.existingSecret` | Nom du Secret contenant `MONGO_URL` | `mongo-hearth-uri` |
| `route.host` | Hostname de la Route publique | `iun-uin-cert-dev.apps.origins.heritage.africa` |
| `replicaCount` | Nombre de replicas | `1` |

## Endpoints publics

- `https://<route.host>/records` — registre HTML de tous les nés/décédés avec IUN
- `https://<route.host>/certificate/birth/{patient_id}` — cert naissance SVG rendu avec données FHIR
- `https://<route.host>/certificate/death/{patient_id}` — cert décès (à venir v2.6)
- `https://<route.host>/status` — métriques poller (cycles, uins_minted, brns_reformatted, drns_reformatted, errors)

## Environnements

- `values-dev.yaml` — 1 replica, ressources light, latest tag
- `values-prod.yaml` — 2 replicas, digest pinné, anti-affinity, annotations Prometheus

## Piège connu

Après `oc start-build`, kubelet cache le tag `:latest` et ne pull pas au rollout restart. **Toujours** patcher par digest explicite via `helm upgrade --set image.digest=<sha256>` ou `oc set image ... @<digest>`.

Voir : `feedback-buildconfig-rebuild-digest` dans la doc troubleshooting.
