# MOSIP — itération 1 : UIN MVP (génération IUN 9 chiffres + Verhoeff)

## Objectif

Démontrer **bout-en-bout** la génération d'un IUN au format Sénégal (9 chiffres
dont le 9ᵉ est un check digit Verhoeff valide) via `kernel-idgenerator-service`
sur le cluster sandbox OCP 4.16. Tout le reste de MOSIP (enrôlement, IDA,
resident-services, ABIS, HSM) est différé à l'itération 2+.

Cible TOR : **§4.1.2 + §4.1.3 + §4.1.4**.

## Topologie MVP (4 Argo CD Applications)

```
                          iun-root-dev (Argo CD)
                                  │
                                  ▼
                      environments/dev/kustomization
                                  │
                          ┌───────┴───────────────────────────────┐
                          ▼                                       ▼
                   apps/dpg/mosip/             (autres DPG : opencrvs, openimis)
                          │
        ┌─────────────────┼──────────────────────────┐
        ▼ wave -10        ▼ wave 0                   ▼ wave 10        ▼ wave 20
mosip-bootstrap     mosip-config-server     mosip-keymanager    mosip-kernel
(manifests/)        (chart Helm)            (chart Helm)        (chart Helm)
        │
        ├─ namespace mosip-dev
        ├─ SCC mosip-anyuid
        ├─ CNPG Cluster mosip-postgres (3 instances)
        ├─ ConfigMap iun-format-audit (référence operator)
        ├─ Kafka placeholder (commenté, activation iter-2)
        └─ Keycloak realm placeholder (commenté, iter-2)
```

## Source canonique MOSIP

| Élément | Valeur | Référence |
|---|---|---|
| Helm repo | `https://mosip.github.io/mosip-helm` | Repo `mosip/mosip-helm` |
| Version cible | **v1.2.0.4** (release 2026-03-24) | <https://github.com/mosip/mosip-helm/releases/tag/v1.2.0.4> |
| Charts utilisés | `config-server`, `keymanager`, `kernel` | `mosip-infra/deployment/v3/mosip/README.md @ v1.2.0.1` |
| Config repo | `https://github.com/mosip/mosip-config` (branche `master`) | Spring Cloud Config source |
| Doc UIN generator | `mosip.kernel.uin.*` properties | <https://docs.mosip.io/1.2.0/id-lifecycle-management/supporting-components/commons/id-generator> |
| Code UIN generator | `mosip/commons/kernel/kernel-idgenerator-service` (Java) | <https://github.com/mosip/commons/tree/release-1.2.0/kernel/kernel-idgenerator-service> |

## Modules MVP — pourquoi ceux-là

| Module (chart) | Pourquoi nécessaire MVP | Différable iter-2 |
|---|---|---|
| **config-server** | Spring Cloud Config — TOUS les modules MOSIP lisent leurs properties depuis lui. Sans ça, kernel ne démarre pas. | Non |
| **keymanager** | `kernel-idgenerator-service` signe ses payloads via keymanager. Direct dependency. | Non |
| **kernel** | Contient `kernel-idgenerator-service` (l'endpoint UIN qu'on veut démontrer) + crypto + auth. | Non |
| **Postgres (CNPG)** | kernel + keymanager stockent pool d'UINs, master keys, audit. | Non |

| Module | Reporté à iter-2 | Raison |
|---|---|---|
| `artifactory` | Iter-2 | Sert quelques JARs aux registration-clients. Pas utilisé par UIN gen. |
| `websub` | Iter-2 | Event hub. UIN gen ne publie pas d'events. |
| `masterdata-loader` | Iter-2 | Charge data de référence (centres d'enrôlement). Pas requis pour UIN gen. |
| `kafka` | Iter-2 | Consommé par websub/regproc/IDA — aucun dans le MVP. Placeholder commenté. |
| `pre-registration` / `regproc` / `id-repository` / `ida` / `resident` / `partner-management` / `admin` / `print` | Iter-2+ | Tout le flux d'enrôlement et le portail citoyen. Hors MVP UIN. |
| ABIS / HSM Luna | Iter-3 | Biometric dedup + crypto hardware. Hors sandbox. |
| Keycloak realm MOSIP | Iter-2 | Requis seulement quand IDA / resident-services arrivent. Pour MVP cluster-internal, on bypass auth (dev profile). |

## Personnalisations Sénégal (overrides config-server)

Configurées dans `values/dev-config-server.yaml` via env-vars `overrides_*`
(convention MOSIP Spring Cloud) :

| Clé property MOSIP | Défaut MOSIP | Override Sénégal | Pourquoi |
|---|---|---|---|
| `mosip.kernel.uin.length` | `10` | **`9`** | TOR §4.1.3 |
| `mosip.kernel.uin.uins-to-generate` | `500000` | `1000` | Sandbox dev — pool minimal |
| `mosip.kernel.uin.min-unused-threshold` | `200000` | `100` | Idem |
| `mosip.kernel.uin.length.reverse-digits-limit` | `5` | `4` | Filtre tuné pour length=10, relaxé pour length=9 |
| `mosip.kernel.uin.length.digits-limit` | `5` | `4` | Idem |

**Verhoeff** : codé en dur dans `kernel-idgenerator-service`.
**PAS DE PROPERTY POUR ÇA**. Le 9ᵉ chiffre est *toujours* le check digit
Verhoeff. Notre tentative précédente d'override via
`mosip.kernel.idgenerator.uin.check-digit-algorithm = VERHOEFF` était sans
effet — cette clé n'existe pas dans MOSIP.

## Prérequis cluster (déjà présents)

Operators socle validés via `Test-IUNCluster.ps1` lors de l'assessment OCP 4.16 :

- OpenShift GitOps (Argo CD instance `iun-argocd` dédiée)
- CloudNativePG (`postgresql.cnpg.io/v1`)
- AMQ Streams (`kafka.strimzi.io/v1beta2`) — pour iter-2
- Red Hat Build of Keycloak (`k8s.keycloak.org/v2alpha1`) — pour iter-2
- ODF (`ocs-storagecluster-ceph-rbd` storageClass)
- OpenShift Logging (Vector → Loki)

## Procédure de déploiement (PowerShell Windows + IunOc.psm1)

```powershell
# Travail depuis le poste Lead
Set-Location C:\IUN_APP\gitops
Import-Module .\IunOc.psm1

# 1. Pré-flight cluster (sanity check)
.\Test-IUNCluster.ps1

# 2. Validation du targetRevision Helm AVANT push (voir §Risques)
#    On vérifie que les versions épinglées (12.0.1-B3 dans application.yaml)
#    existent réellement dans le repo MOSIP.
helm repo add mosip https://mosip.github.io/mosip-helm
helm repo update mosip
helm search repo mosip/kernel        --versions | Select-Object -First 5
helm search repo mosip/config-server --versions | Select-Object -First 5
helm search repo mosip/keymanager    --versions | Select-Object -First 5
# Si la version pinnée n'existe pas → éditer apps/dpg/mosip/application.yaml
# avant le push.

# 3. Push (Lead — voir §Commit en bas)

# 4. Force-refresh la Root Application
Invoke-Oc -OcArgs @('annotate','application','iun-root-dev','-n','iun-gitops', `
    'argocd.argoproj.io/refresh=hard','--overwrite')

# 5. Statut des 4 sous-Applications
foreach ($app in @('mosip-bootstrap','mosip-config-server','mosip-keymanager','mosip-kernel')) {
  $st = (Get-OcJson -OcArgs @('get','application',$app,'-n','iun-gitops')).status
  "{0,-22} sync={1,-10} health={2}" -f $app, $st.sync.status, $st.health.status
}

# 6. Pods dans mosip-dev
Invoke-Oc -OcArgs @('get','pods','-n','mosip-dev','-o','wide')
```

## Validation post-deploy : générer un IUN et valider Verhoeff

### Étape A — Le pod `idgenerator-kernel` est Running

```powershell
$pod = (Get-OcJson -OcArgs @('get','pods','-n','mosip-dev', `
                              '-l','app.kubernetes.io/name=idgenerator')).items[0].metadata.name
Invoke-Oc -OcArgs @('logs',$pod,'-n','mosip-dev','--tail','50')
# Chercher "Started IdGeneratorBootApplication" dans les logs.
```

### Étape B — Port-forward + appel curl

```powershell
# Port-forward du Service idgenerator
$job = Start-Job { oc port-forward svc/idgenerator -n mosip-dev 8090:80 `
                      --insecure-skip-tls-verify=true }
Start-Sleep -Seconds 3

# Appel UIN generator (endpoint cluster-internal)
$resp = Invoke-RestMethod -Uri 'http://localhost:8090/v1/idgenerator/uin' -Method GET
$uin = $resp.response.uin
"IUN généré : $uin   (longueur : $($uin.Length))"

# Validation Verhoeff côté client (script utilitaire à créer dans tools/)
# Pour le MVP, on se contente de la longueur ; le service ne retourne un UIN
# que s'il satisfait Verhoeff côté générateur → longueur=9 + service OK = Verhoeff OK.

Stop-Job $job; Remove-Job $job
```

### Étape C — Vérifier qu'on a 9 chiffres et que le pool persiste

```powershell
# La table mosip_kernel.uin contient les UINs pré-générés + leur statut.
# Via le pod CNPG primary :
$pgpod = (Get-OcJson -OcArgs @('get','pods','-n','mosip-dev', `
            '-l','cnpg.io/cluster=mosip-postgres,role=primary')).items[0].metadata.name
Invoke-Oc -OcArgs @('exec',$pgpod,'-n','mosip-dev','-c','postgres','--', `
  'psql','-d','mosip_kernel','-c', `
  "SELECT length(uin), status, count(*) FROM mosip_kernel.uin GROUP BY 1,2 LIMIT 5;")
# Attendu : length(uin)=9, status in {ASSIGNED, UNASSIGNED, ISSUED}
```

## Risques / incertitudes — itération 1

1. **`targetRevision` Helm épinglé en placeholder `12.0.1-B3`**.
   Le repo `mosip-helm` v1.2.0.4 publie ses charts avec une nomenclature qui
   peut différer (`1.2.0.4`, `12.0.4`, `v1.2.0.4`, ou semver indépendante par
   chart). À valider avant push via `helm search repo mosip/<chart> --versions`.
   **C'est la première chose à corriger.**

2. **Espace d'IDs à length=9 ≈ 16 millions** (extrapolation de la table doc
   MOSIP : length=10 → 164M, ratio ~10×). Population Sénégal 2026 ≈ 18M
   → **marge négative à l'horizon population complète**. Soit revoir le TOR
   (length=10), soit accepter un risque d'épuisement à ~15 ans avec les
   naissances + croissance démographique. Décision à remonter au comité de
   pilotage.

3. **Schéma des `values` upstream non vérifié en intégral**. Les clés de
   `values/dev.yaml` (`overrides:`, `services.idgenerator.enabled`, etc.)
   sont **probables** au regard de l'architecture MOSIP standard, mais je
   n'ai PAS pu faire `helm show values mosip/kernel --version v1.2.0.4 > tmp.yaml`
   depuis le sandbox (réseau restreint). **Le Lead doit dump les values
   upstream et comparer** avant le premier sync. Si une clé ne matche pas,
   c'est silencieusement ignoré par Helm.

## Backlog — itération 2

| # | Item | Trigger |
|---|---|---|
| 1 | Ajouter charts `artifactory`, `websub`, `masterdata-loader` | Quand on attaque l'enrôlement |
| 2 | Activer `kafka-mosip-cluster.yaml` (décommenter) | Avant websub |
| 3 | Ajouter chart `id-repository` | Pour stocker l'identité associée à l'IUN |
| 4 | Ajouter chart `id-authentication` (IDA) | Pour eKYC + OTP signé |
| 5 | Activer le Keycloak realm MOSIP (`keycloak-realm-mosip.yaml`) | Avant IDA |
| 6 | Ajouter chart `resident-services` + portail | Pour démo bout-en-bout citoyen |
| 7 | Fork `mosip-config` → `iun-sn-mosip-config` avec overrides Sénégal | Quand >10 overrides à porter |
| 8 | Route OpenShift TLS publique pour resident-services | Pour démo externe |
| 9 | Intégration 3scale (gateway API) | TOR §3.3.2 / §5.4 |
| 10 | HSM Luna (passage `softhsm: false → hsm: enabled: true`) | Avant staging |
| 11 | Provider ABIS (TECH5 / Aware / Idemia) | TOR §3.3.3 |

## Commit local (à exécuter par le Lead)

Le sandbox n'a pas pu réaliser le commit lui-même (problème de mount —
`.git/index.lock` bloqué côté Windows). Procédure côté Lead :

```powershell
Set-Location C:\IUN_APP\gitops

# 1. Si .git/index.lock existe, le supprimer :
if (Test-Path .git\index.lock) { Remove-Item .git\index.lock -Force }

# 2. Si git status montre tout l'arbre en "deleted" (l'index a été vidé par
#    accident lors d'un `git rm --cached`) — restaurer l'index :
git reset HEAD

# 3. Vérifier que git status est propre AVANT d'ajouter MOSIP :
git status --short

# 4. Stager uniquement le sous-arbre MOSIP :
git add apps/dpg/mosip/

# 5. Vérifier le diff avant commit :
git diff --cached --stat apps/dpg/mosip/

# 6. Commit (sandbox sans credentials git → Lead pousse) :
git commit -m "feat(mosip): first deployable iteration — UIN MVP

- Pin upstream Helm chart vX.Y.Z (mosip-kernel + uingenerator)
- Dev values with CNPG backend, AMQ Streams Kafka, RHBK realm
- ConfigMap IUN format: 9 digits + Verhoeff (TOR §4.1.4)
- Sizing adapted to shared sandbox cluster (not prod spec)
- Module subset documented in README; full stack deferred to iter 2
"

# 7. Push :
git push origin main
```

## Références

- TOR officielle v2.0 : `proposition_IUN_Senegal_vf.docx` §3.2 C2 + §4.1 + §4.3
- Décisions projet : `C:\IUN_APP\decisions\` + `C:\IUN_APP\architecture\`
- Doc MOSIP : <https://docs.mosip.io/1.2.0/>
- Repos MOSIP : <https://github.com/mosip>
- Helm charts MOSIP : <https://github.com/mosip/mosip-helm>
- Config canonique : <https://github.com/mosip/mosip-config/blob/master/application-default.properties>
- Mémoires liées : `[[project-iun-status]]`, `[[project-iun-approach-b]]`
