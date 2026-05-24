# OpenIMIS — Insurance Management Information System (DPG)

> **Statut iter 1 (mai 2026)** : MVP déployable produit — option **A**
> (kompose convert + Kustomize OCP-adapté). Voir §1 pour la justification.

## Rôle dans IUN Sénégal

OpenIMIS gère la **protection sociale** : affiliations, cotisations,
prestations des grands régimes sénégalais. **L'IUN MOSIP est la clé primaire**
de toutes les affiliations. Référence TOR `proposition_IUN_Senegal_vf.docx` :

- **§3.2 C3** — Composant 3 : protection sociale (OpenIMIS)
- **§4.4** — Régimes couverts : CMU (santé), PNBSF (bourse), IPRES (retraite), CSS (famille)
- **§3.4.1** — Enrôlement nouveau-né déclenche ouverture droits CMU automatique
- **§4.4** — Sync hebdomadaire IPRES, temps réel CSS, quotidienne CMU/MSAS
- **§4.4** — Ordres de virement Trésor Public pour PNBSF, paiements mobiles Wave + Orange Money

---

## 1. Décision de packaging (A / B / C)

OpenIMIS **ne publie pas** de Helm chart upstream officiel ; le déploiement
canonique est `openimis-dist_dkr` (docker-compose all-in-one).
Source : <https://github.com/openimis/openimis-dist_dkr> (release **25.10**,
Nov 2025). Trois options ont été évaluées :

### Option A — `kompose convert` + Kustomize OCP-adapté

Adapter le `compose.{yml,base,postgresql,openSearch,cache}.yml` officiel via
`kompose convert`, puis relire chaque manifeste à la main pour :

- supprimer Traefik (remplacé par Route OCP),
- ajouter healthchecks K8s natifs,
- externaliser tous les credentials en `Secret`,
- forcer `restricted-v2` / `nonroot-v2` (allowPrivilegeEscalation=false, drop ALL caps, runAsNonRoot),
- promouvoir le volume `photos` en PVC RWX (ODF CephFS), partagé backend/worker,
- ajouter Service + Route OCP pour le frontend.

| Critère | Évaluation |
|---|---|
| Effort | **2-3 j/h** pour MVP propre |
| Pérennité | Moyenne — à chaque release upstream, refaire conversion (ou maintenir manuellement les manifestes en diff) |
| Risques | Manifestes spécifiques à maintenir hors upstream ; pas d'umbrella `values.yaml` |
| Alignement TOR §4 (ouverture / neutralité) | OK — chemin officiel, AGPL respectée, traçabilité claire vers `openimis-dist_dkr` |

### Option B — Fork communautaire Helm

Cibler un fork communautaire fournissant un Helm chart. **Recherche effectuée
(GitHub, ArtifactHub, GitLab) le 24 mai 2026 : aucun fork crédible et maintenu
n'a été identifié** (le repo upstream a 51 forks au moment de l'audit, aucun
n'expose un Helm chart publiquement référencé).

| Critère | Évaluation |
|---|---|
| Effort | Faible *si* cible existe — **bloqué** sans candidat |
| Pérennité | Risque élevé d'orphelinat |
| Risques | Sécurité du fork, divergence upstream |
| Alignement TOR | Médiocre (dépendance à un mainteneur tiers non identifié) |

→ **Option non actionnable** en T+0.

### Option C — Contribution upstream d'un Helm chart

Co-construire avec la communauté OpenIMIS (Atlassian wiki, canal #deployment)
un Helm chart officiel.

| Critère | Évaluation |
|---|---|
| Effort | **3-6 mois** (review communauté + cycle de release) |
| Pérennité | Excellente |
| Risques | Calendrier hors notre contrôle |
| Alignement TOR §4 (contribution DPG) | Excellent — capitalisation pour la communauté DPG |

→ **À programmer en track 2 / iter 3+** comme évolution de l'option A.

### **Décision retenue : Option A** (avec piste C en parallèle)

L'option A est la seule actionnable à court terme. Elle livre un MVP
déployable sous 3 jours et nous permet d'attaquer P1 du programme.
Les manifestes que nous produisons aujourd'hui constituent en outre le
**brouillon** d'un futur chart Helm contribué à l'upstream (track C).

---

## 2. Source officielle et version retenue

| Élément | Valeur |
|---|---|
| Distribution all-in-one | <https://github.com/openimis/openimis-dist_dkr> |
| Release retenue | **25.10** (11 nov. 2025) |
| Backend (Django) | `ghcr.io/openimis/openimis-be:25.10` |
| Frontend (React + NGINX) | `ghcr.io/openimis/openimis-fe:25.10` |
| DB custom (Postgres + schéma OpenIMIS) | `ghcr.io/openimis/openimis-pgsql:25.10` |
| Helm chart officiel | **N'EXISTE PAS** (cf. §1) |
| Documentation | <https://openimis.atlassian.net/wiki/> |
| Site projet | <https://openimis.org/> |
| Licence upstream | GNU AGPL v3 |

---

## 3. Topologie déployée (MVP iter 1)

```
                  Route OCP (TLS edge)
                          |
                          v
                  openimis-frontend   (Deployment x1, port 8080)
                          |
                          v  /api
                  openimis-backend    (Deployment x1, port 8000)
                          |
        +-----------------+----------------+-----------------+
        v                 v                v                 v
   openimis-db      openimis-redis  openimis-rabbitmq  openimis-opensearch
   (StatefulSet)    (Deployment)    (StatefulSet)      (StatefulSet)
   PVC 20Gi RBD     ephemeral       PVC 5Gi RBD        PVC 30Gi RBD

   openimis-worker  (Deployment x2)  -- consomme amqp:5672 (rabbitmq)
                                     -- lit/ecrit /photos (PVC RWX 5Gi cephfs)

   openimis-opensearch-dashboards (Deployment x1, port 5601)
```

Tous les pods utilisent la ServiceAccount `openimis-sa` (SCC `nonroot-v2`).
Le PVC `openimis-photos` est monté en RWX sur backend ET worker (le compose
officiel utilise un volume Docker partagé `photos`).

**Note :** la Route OCP expose `openimis-frontend` ; le backend, redis, rabbit,
opensearch restent en `ClusterIP` (pas d'exposition externe en iter 1).

---

## 4. Inventaire des fichiers livrés

```
apps/dpg/openimis/
├── application.yaml                     <- Argo CD App (pointe sur manifests/)
├── kustomization.yaml                   <- wrap iun-gitops namespace
├── README.md                            <- ce fichier
├── values/
│   ├── dev.yaml                         <- referentiel logique (PAS un chart Helm)
│   ├── staging.yaml                     <- placeholder phase pilote
│   └── prod.yaml                        <- placeholder phase nationale
└── manifests/                           <- cible Argo CD (Kustomize plain)
    ├── kustomization.yaml               <- assemble tout
    ├── namespace.yaml                   <- (existant) openimis-dev
    ├── 00-serviceaccount.yaml           <- SA openimis-sa
    ├── 01-secrets-dev.yaml              <- creds PoC (a externaliser iter 2)
    ├── 02-configmap-app.yaml            <- env generique OpenIMIS
    ├── 03-configmap-openimis-sn.yaml    <- parametrage metier Senegal (4 regimes, 14 regions)
    ├── 10-postgres.yaml                 <- StatefulSet + Service
    ├── 11-redis.yaml                    <- Deployment + Service (cache ephemere)
    ├── 12-rabbitmq.yaml                 <- StatefulSet + Service (PVC 5Gi)
    ├── 13-opensearch.yaml               <- StatefulSet + Service (PVC 30Gi)
    ├── 14-opensearch-dashboards.yaml    <- Deployment + Service
    ├── 20-photos-pvc.yaml               <- PVC RWX cephfs (partage BE+Worker)
    ├── 21-backend-migrations.yaml       <- Job Argo CD hook Sync
    ├── 22-backend-api.yaml              <- Deployment + Service Django
    ├── 23-backend-worker.yaml           <- Deployment Celery (x2)
    ├── 30-frontend.yaml                 <- Deployment + Service React/NGINX
    └── 31-frontend-route.yaml           <- Route OCP TLS edge
```

---

## 5. Procédure de déploiement (PowerShell, Lead Senior Dev)

> ⚠ **Prérequis** : SCC `nonroot-v2` lié à la ServiceAccount `openimis-sa`
> (commande admin cluster, **hors GitOps**) :
>
> ```powershell
> Import-Module C:\IUN_APP\gitops\IunOc.psm1
> Test-OcAccess
> # Creer le namespace (sinon Argo le cree au premier sync — mais SA doit y etre)
> Invoke-Oc create namespace openimis-dev --dry-run=client -o yaml |
>   Invoke-Oc apply -f -
> # Lier le SCC
> Invoke-Oc adm policy add-scc-to-user nonroot-v2 `
>   -z openimis-sa -n openimis-dev
> ```

```powershell
# 1. Health-check prealable du cluster (cert API, Operators)
Set-Location C:\IUN_APP\gitops
.\Test-IUNCluster.ps1

# 2. Commit + push gitops (push fait par le Lead — voir §8 du presentation finale)
git status
git log -1 --oneline

# 3. Forcer un refresh hard de l'Application Argo CD
Import-Module .\IunOc.psm1
Invoke-Oc annotate application openimis-core -n iun-gitops `
  argocd.argoproj.io/refresh=hard --overwrite

# 4. Suivre la convergence
Get-OcJson get application openimis-core -n iun-gitops |
  Select-Object -ExpandProperty status |
  Select-Object sync, health

# 5. Lister les pods du namespace cible
Invoke-Oc get pods -n openimis-dev -w
```

---

## 6. Validation post-déploiement

### Checks d'infrastructure

```powershell
# Tous les pods Running / Ready
Invoke-Oc get pods -n openimis-dev `
  -o jsonpath="{range .items[*]}{.metadata.name}{'\t'}{.status.phase}{'\n'}{end}"

# Job de migration termine Successfully
Get-OcJson get job openimis-be-migrations -n openimis-dev |
  Select-Object -ExpandProperty status

# Route exposee
Invoke-Oc get route openimis-frontend -n openimis-dev -o jsonpath="{.spec.host}"
```

Attendu : `openimis-dev.apps.origins.heritage.africa` (ou ce que le router
OCP a effectivement attribué).

### Check fonctionnel MVP

1. Ouvrir `https://openimis-dev.apps.origins.heritage.africa` dans un navigateur.
2. Login admin OpenIMIS (credentials initialisés par l'image `openimis-pgsql:25.10`
   avec `DEMO_DATASET=True` : login `Admin` / mot de passe par défaut documenté
   par l'upstream — voir wiki MO2.1).
3. **Création d'une famille de test** : Menu → Insurees → New family → renseigner
   un chef de famille avec un IUN factice + 1 ayant droit.
4. **Création d'une police de couverture** : Menu → Policies → New policy →
   attacher à la famille créée, choisir produit `Demo` (CMU sera ajouté au seed iter 2).
5. **Création d'une demande de prestation (claim)** : Menu → Claims → New claim →
   sélectionner l'assuré, ajouter un service/médicament, soumettre.
6. Vérifier que le claim apparaît dans la liste de revue.

Si les 6 étapes passent → **MVP iter 1 validé**.

---

## 7. Backlog iter 2 (priorisé)

1. **Seed Sénégal** — Job Kustomize qui charge `03-configmap-openimis-sn.yaml`
   dans la DB OpenIMIS : créer les 4 `Product` (CMU, PNBSF, IPRES, CSS) et les
   14 régions / 46 départements officiels. Voir `apps/integration/seed-openimis-sn/`.
2. **Externalisation des Secrets** — remplacer `01-secrets-dev.yaml` par des
   `ExternalSecret` pointant Vault ou ESO (External Secrets Operator).
3. **Webhook MOSIP → OpenIMIS** : à l'enrôlement IUN, créer un `Insuree` OpenIMIS
   avec l'IUN comme clé. À implémenter dans `apps/integration/mosip-to-openimis/`.
4. **Webhook OpenCRVS → OpenIMIS** : à l'enregistrement de naissance, ouvrir
   les droits CMU (TOR §3.4.1).
5. **Connecteurs externes** : IPRES (sync hebdo), CSS (temps réel via 3scale),
   CMU/MSAS (quotidien).
6. **Paiements mobiles** : intégration Wave et Orange Money pour PNBSF +
   cotisations CMU (sandbox API d'abord).
7. **Connecteur Trésor Public** : ordres de virement PNBSF — co-définition avec
   Direction Trésor du Sénégal.
8. **Auth RHBK** : remplacer auth interne OpenIMIS par RHBK realm IUN (OIDC).
9. **NetworkPolicies** : isoler `openimis-dev` des autres namespaces, n'autoriser
   que les flux iun-gitops + opencrvs-dev + mosip-dev.
10. **Migration DB vers CNPG** : remplacer `openimis-pgsql` upstream par un cluster
    CNPG géré (sauvegardes Barman, HA), avec Job de bootstrap qui rejoue les migrations.
11. **Track C — contribution upstream** : ouvrir une issue sur
    `openimis/openimis-dist_dkr` proposant ce chart Kustomize comme base
    pour un futur Helm chart officiel.

---

## 8. Risques et dette

### Risques majeurs (à remonter au Comité de pilotage)

1. **Absence de Helm chart upstream** — toute évolution d'OpenIMIS impose
   de retraiter les manifestes manuellement. Mitigation : track C (contribution),
   et discipline de pin sur version `25.10` jusqu'à validation pilote.
2. **OpenSearch sur OCP** — nécessite `vm.max_map_count >= 262144` (sysctl host).
   En sandbox, on tolère l'instabilité au-delà de quelques milliers de docs.
   Mitigation staging : Tuned profile cluster pour relever le sysctl.
3. **SCC OCP vs images upstream** — si le frontend NGINX upstream ne sait pas
   binder sur 8080 (le compose binde 80/443), il faudra soit patcher la conf
   NGINX dans une `ConfigMap` overlay, soit escalader vers SCC `anyuid`
   (acceptable en dev, à valider sécurité pour prod).
4. **Photos partagées RWX** — requiert ODF CephFS opérationnel. À tester en
   priorité : un photo upload via UI doit être visible par le worker.

### Dette technique reconnue

- Credentials en clair dans `01-secrets-dev.yaml` (PoC only — iter 2).
- `DEMO_DATASET=True` charge un jeu de démo OpenIMIS qui contient des produits
  étrangers. À purger avant tout pilote Sénégal.
- `migrations` Job ne gère pas le rollback (Argo CD `BeforeHookCreation` recrée
  systématiquement — peut bloquer un sync en cas d'échec).
- Worker healthcheck `pgrep -f celery` est fragile (faux positif possible si
  worker hang) — à remplacer par un `celery inspect ping` propre dès iter 2.
- Aucune `NetworkPolicy` — namespace ouvert à tout le cluster en iter 1.

---

## 9. Références

- TOR officielle v2.0 : `proposition_IUN_Senegal_vf.docx`
- Document stratégique de pivot : `C:\IUN_APP\architecture\REPENSEE-PROJET-v1.md`
- Distribution Docker upstream : <https://github.com/openimis/openimis-dist_dkr>
- compose.base.yml référence : <https://github.com/openimis/openimis-dist_dkr/blob/develop/compose.base.yml>
- compose.postgresql.yml : <https://github.com/openimis/openimis-dist_dkr/blob/develop/compose.postgresql.yml>
- compose.cache.yml : <https://github.com/openimis/openimis-dist_dkr/blob/develop/compose.cache.yml>
- compose.openSearch.yml : <https://github.com/openimis/openimis-dist_dkr/blob/develop/compose.openSearch.yml>
- Backend repo : <https://github.com/openimis/openimis-be_py>
- Frontend repo : <https://github.com/openimis/openimis-fe_js>
- Maintainer Guide : <https://openimis.atlassian.net/wiki/spaces/OP/pages/4468768808/Maintainer+Guide>
- Installation Docker (MO1.1) : <https://openimis.atlassian.net/wiki/spaces/OP/pages/963182705/MO1.1+Install+the+modular+openIMIS+using+Docker>
- Sources Release 2026-04 : <https://openimis.atlassian.net/wiki/spaces/OP/pages/4653678593>
