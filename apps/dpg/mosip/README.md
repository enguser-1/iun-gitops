# MOSIP — itération 1 (extension v2) : UIN MVP + dépendances ConfigMap

## Objectif

Démontrer **bout-en-bout** la génération d'un IUN au format Sénégal (10 chiffres
dont le 10ᵉ est un check digit Verhoeff valide — défaut MOSIP, ADR-R01 du
2026-05-25) via `kernel-idgenerator-service` sur le cluster sandbox OCP 4.16.
Tout le reste de MOSIP (enrôlement, IDA, resident-services, ABIS, HSM Luna)
est différé à l'itération 2+.

Cible TOR : **§4.1.2 + §4.1.3 + §4.1.4**.

## Historique : pourquoi cette v2

La v1 du scaffold (3 Apps Helm + bootstrap manifests) a été syncée le 2026-05-24
sur `api.origins.heritage.africa:6443` / ns `mosip-dev`. Les images sont tirées
correctement, mais **les pods keymanager et idgenerator sont restés en
`CreateContainerConfigError` pendant 3h47**. Diagnostic :

| ConfigMap référencée en envFrom | Statut au sync v1 |
|---|---|
| `global`               | manquante |
| `config-server-share`  | créée par config-server (OK) |
| `artifactory-share`    | manquante |
| `softhsm-kernel-share` | manquante |

MOSIP a un couplage fort sur les ConfigMaps "share" : chaque module produit
la sienne, tous les autres consommateurs en dépendent en envFrom. Le scaffold
v1 ne déclarait pas les charts producteurs.

**v2 ajoute 3 Argo CD Applications** (`mosip-global`, `mosip-artifactory`,
`mosip-softhsm-kernel`) AVANT config-server, et rebumpe les waves existantes
pour respecter l'ordre canonique.

## Topologie étendue (7 Argo CD Applications)

```
                         iun-root-dev (Argo CD)
                                 │
                                 ▼
                  environments/dev/kustomization
                                 │
                  apps/dpg/mosip/ (cette Kustomization)
                                 │
   ┌──────────┬─────────┬───────────────┬───────────────────┬─────────────┬─────────────┬──────────────┐
   ▼ wave -10 ▼ wave 5  ▼ wave 6        ▼ wave 7            ▼ wave 10    ▼ wave 20    ▼ wave 20
mosip-       mosip-    mosip-          mosip-              mosip-       mosip-       mosip-
bootstrap    global    artifactory     softhsm-kernel      config-      keymanager   idgenerator
(manifests/) (CM       (chart Helm     (chart Helm         server       (chart Helm) (chart Helm)
             global)   mosip/          mosip/softhsm       (chart Helm)
                       artifactory)    releaseName=
                                       softhsm-kernel)
   │           │            │                  │                │             │             │
   │           ▼            ▼                  ▼                ▼             ▼             ▼
   │       ConfigMap    ConfigMap         ConfigMap        ConfigMap     PVC HSM       expose
   │       `global`     `artifactory-     `softhsm-        `config-      tokens        endpoint
   │       (envFrom     share`            kernel-share`    server-       + signe       UIN gen
   │       all MOSIP)   (envFrom          (envFrom         share`        payloads      /v1/idgen/uin
   │                    keymanager+       keymanager)      (envFrom      kernel
   │                    idgenerator)                       all)
   │
   ├─ namespace mosip-dev
   ├─ SCC mosip-anyuid
   ├─ CNPG Cluster mosip-postgres (3 instances)
   ├─ ConfigMap iun-format-audit (référence operator — INFORMATIONNELLE)
   ├─ Kafka placeholder (commenté, iter-2)
   └─ Keycloak realm placeholder (commenté, iter-2)
```

### Ordre de sync canonique (depuis mosip-infra v1.2.0.x)

1. **mosip-bootstrap** (wave -10) — namespace, SCC, CNPG Postgres, CM IUN audit
2. **mosip-global** (wave 5) — ConfigMap `global` cluster-wide
3. **mosip-artifactory** (wave 6) — chart `mosip/artifactory` → CM `artifactory-share`
4. **mosip-softhsm-kernel** (wave 7) — chart `mosip/softhsm` (release name `softhsm-kernel`) → CM `softhsm-kernel-share`
5. **mosip-config-server** (wave 10) — chart `mosip/config-server` → CM `config-server-share`
6. **mosip-keymanager** (wave 20) — chart `mosip/keymanager`
7. **mosip-idgenerator** (wave 20) — chart `mosip/idgenerator`

> Note sur wave 20 partagé entre keymanager et idgenerator : Argo CD lance les
> deux en parallèle puis attend Healthy sur les deux avant de passer au wave
> suivant. idgenerator peut transitoirement CrashLoop pendant 30-60 s le temps
> que keymanager devienne Ready (dépendance crypto pour signer les payloads).
> Si ça pose souci en debug, séparer en wave 20 / wave 30.

## Source canonique MOSIP

| Élément | Valeur | Référence |
|---|---|---|
| Helm repo | `https://mosip.github.io/mosip-helm` | Repo `mosip/mosip-helm` |
| Version cible | **v1.2.0.4** (release 2026-03-24) | <https://github.com/mosip/mosip-helm/releases/tag/v1.2.0.4> |
| Charts utilisés v2 | `artifactory`, `softhsm`, `config-server`, `keymanager`, `idgenerator` | `mosip-infra/deployment/v3` @ release-1.2.0.x |
| ConfigMap `global` | **PAS un chart** — manifeste pur `mosip/k8s-infra` | <https://github.com/mosip/k8s-infra/blob/main/mosip/global_configmap.yaml.sample> |
| Config repo | `https://github.com/mosip/mosip-config` (branche `master`) | Spring Cloud Config source |
| Artifactory image | `mosipid/artifactory-server` | <https://github.com/mosip/artifactory-ref-impl> |
| SoftHSM install ref | `mosip-infra/deployment/v3/external/hsm/softhsm` | <https://github.com/mosip/mosip-infra/blob/v1.2.0.2/deployment/v3/external/hsm/softhsm/README.md> |
| Doc deployment | `docs.mosip.io 1.2.0` | <https://docs.mosip.io/1.2.0/setup/deploymentnew/v3-installation/mosip-external-dependencies> |
| Doc UIN generator | `mosip.kernel.uin.*` properties | <https://docs.mosip.io/1.2.0/id-lifecycle-management/supporting-components/commons/id-generator> |
| Code UIN generator | `mosip/commons/kernel/kernel-idgenerator-service` (Java) | <https://github.com/mosip/commons/tree/release-1.2.0/kernel/kernel-idgenerator-service> |

## Modules ajoutés en v2 — pourquoi

| Chart | Rôle MVP | ConfigMap produite | Dette technique |
|---|---|---|---|
| `mosip/artifactory` | File server interne distribuant artefacts runtime (mock keys dev, ID schemas, application-default.properties). | `artifactory-share` (envFrom keymanager + idgenerator) | En dev : sert des **mock keys non-prod**. En staging+ : artifactory dédié servant artefacts signés Luna. |
| `mosip/softhsm` (releaseName=`softhsm-kernel`) | SoftHSM2 logiciel pour la crypto kernel (signature, dérivation). | `softhsm-kernel-share` (envFrom keymanager) | **DEV ONLY**. Pin `iun-dev-pin` stocké en clair dans CM. Migration HSM Luna FIPS 140-2 L3 obligatoire pré-staging (TOR §4.3.2). |
| ConfigMap `global` (manifeste, pas chart) | Config cluster-wide (hostnames, pins HSM, profils Spring). | (elle-même) | Pins HSM en clair → idem softhsm. |

## Personnalisations Sénégal (overrides config-server) — ADR-R01 2026-05-25

Configurées dans `values/dev-config-server.yaml` via env-vars `overrides_*`
(convention MOSIP Spring Cloud). Suite à l'ADR-R01 (2026-05-25), le format
est aligné sur le défaut MOSIP (10 chiffres) — l'ancien override 9 chiffres
a été abandonné, démontré mathématiquement impossible. Aucun override de
filtre anti-pattern n'est désormais nécessaire :

| Clé property MOSIP | Défaut MOSIP | Valeur Sénégal | Pourquoi |
|---|---|---|---|
| `mosip.kernel.uin.length` | `10` | `10` | TOR §4.1.3 (révisé ADR-R01) — défaut MOSIP |
| `mosip.kernel.uin.uins-to-generate` | `500000` | `1000` | Sandbox dev — pool minimal |
| `mosip.kernel.uin.min-unused-threshold` | `200000` | `100` | Idem |
| `mosip.kernel.uin.length.reverse-digits-limit` | `5` | `5` | Défaut MOSIP conservé |
| `mosip.kernel.uin.length.digits-limit` | `5` | `5` | Défaut MOSIP conservé |

**Verhoeff** : codé en dur dans `kernel-idgenerator-service`.
**PAS DE PROPERTY POUR ÇA**. Le 10ᵉ chiffre est *toujours* le check digit
Verhoeff.

## Prérequis cluster (déjà présents)

Operators socle validés via `Test-IUNCluster.ps1` lors de l'assessment OCP 4.16 :

- OpenShift GitOps (Argo CD instance `iun-argocd` dédiée)
- CloudNativePG (`postgresql.cnpg.io/v1`)
- AMQ Streams (`kafka.strimzi.io/v1beta2`) — pour iter-2
- Red Hat Build of Keycloak (`k8s.keycloak.org/v2alpha1`) — pour iter-2
- ODF (`ocs-storagecluster-ceph-rbd` storageClass) — utilisée par artifactory PVC + softhsm PVC
- OpenShift Logging (Vector → Loki)

## Procédure de déploiement (PowerShell Windows + IunOc.psm1)

```powershell
# Travail depuis le poste Lead
Set-Location C:\IUN_APP\gitops
Import-Module .\IunOc.psm1

# 1. Pré-flight cluster (sanity check)
.\Test-IUNCluster.ps1

# 2. Validation des targetRevision Helm AVANT push (voir §Risques)
#    C'EST LA PREMIÈRE CHOSE À FAIRE — les versions épinglées dans
#    application.yaml sont des PLACEHOLDERS (12.0.4 / 12.1.0 / 12.0.2).
helm repo add mosip https://mosip.github.io/mosip-helm 2>$null
helm repo update mosip
foreach ($chart in @('artifactory','softhsm','config-server','keymanager','idgenerator')) {
  "----- $chart -----"
  helm search repo "mosip/$chart" --versions | Select-Object -First 5
}
# Si une version pinnée n'existe pas → éditer apps/dpg/mosip/application.yaml
# avant le push.

# 3. (Optionnel) Dump des values upstream pour valider le schéma local
foreach ($chart in @('artifactory','softhsm','config-server','keymanager','idgenerator')) {
  helm show values "mosip/$chart" > "C:\IUN_APP\tmp\$chart-upstream-values.yaml"
}
# Puis diff manuel avec apps/dpg/mosip/values/dev-*.yaml.

# 4. Push (Lead — voir §Commit en bas)

# 5. Force-refresh la Root Application
Invoke-Oc -OcArgs @('annotate','application','iun-root-dev','-n','iun-gitops', `
    'argocd.argoproj.io/refresh=hard','--overwrite')

# 6. Statut des 7 sous-Applications (suivre l'ordre de sync-wave)
$apps = @('mosip-bootstrap','mosip-global','mosip-artifactory','mosip-softhsm-kernel', `
          'mosip-config-server','mosip-keymanager','mosip-idgenerator')
foreach ($app in $apps) {
  $st = (Get-OcJson -OcArgs @('get','application',$app,'-n','iun-gitops')).status
  "{0,-25} sync={1,-10} health={2}" -f $app, $st.sync.status, $st.health.status
}

# 7. Pods dans mosip-dev (avec leur état)
Invoke-Oc -OcArgs @('get','pods','-n','mosip-dev','-o','wide')
```

## Validation post-deploy

### Étape 0 — Les 4 ConfigMaps obligatoires existent

C'EST LE PREMIER CHECK À FAIRE — précisément le point qui a coûté 3h47 en v1.

```powershell
$cms = @('global','config-server-share','artifactory-share','softhsm-kernel-share', `
         'iun-format-audit')
foreach ($cm in $cms) {
  $exists = Invoke-Oc -OcArgs @('get','cm',$cm,'-n','mosip-dev','--ignore-not-found','-o','name')
  if ($exists) { "[OK]  $cm" } else { "[KO]  $cm  ← MANQUE" }
}
# Attendu : 5 [OK]. Si l'un est [KO], le pod consommateur restera en
# CreateContainerConfigError. Cause typique : l'Application productrice n'a
# pas encore été syncée (vérifier sync-wave + healthcheck).
```

### Étape A — Le pod `idgenerator` est Running

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

Stop-Job $job; Remove-Job $job
```

### Étape C — Vérifier qu'on a 10 chiffres et que le pool persiste

```powershell
$pgpod = (Get-OcJson -OcArgs @('get','pods','-n','mosip-dev', `
            '-l','cnpg.io/cluster=mosip-postgres,role=primary')).items[0].metadata.name
Invoke-Oc -OcArgs @('exec',$pgpod,'-n','mosip-dev','-c','postgres','--', `
  'psql','-d','mosip_kernel','-c', `
  "SELECT length(uin), status, count(*) FROM mosip_kernel.uin GROUP BY 1,2 LIMIT 5;")
# Attendu : length(uin)=10, status in {ASSIGNED, UNASSIGNED, ISSUED}
```

## Risques / incertitudes — itération 1 (mise à jour v2)

1. **`targetRevision` Helm épinglés en placeholders** pour les 5 charts.
   Le repo `mosip-helm` v1.2.0.4 publie ses charts avec une nomenclature qui
   peut différer chart par chart. À valider AVANT push via `helm search repo
   mosip/<chart> --versions` (cf. §Procédure étape 2). **C'est la première
   chose à corriger si un sync échoue avec "chart not found".**

2. **`global` n'est pas distribuée comme chart upstream.** On la synchronise
   comme manifeste pur depuis `apps/dpg/mosip/manifests/global-configmap/`.
   Conséquence : si MOSIP change le SCHÉMA de la CM `global` dans une future
   release, on doit mettre à jour notre manifeste manuellement (pas de bump
   de chart pour le détecter). À tracker : surveiller diffs de
   <https://github.com/mosip/k8s-infra/blob/main/mosip/global_configmap.yaml.sample>
   entre releases MOSIP.

3. **SoftHSM = dev only.** Pin `iun-dev-pin` en clair dans 2 endroits
   (ConfigMap `global` + ConfigMap `softhsm-kernel-share`). N'A AUCUNE
   valeur cryptographique au sens prod. À ARRACHER avant staging — passage
   obligatoire à HSM Luna FIPS 140-2 L3 (TOR §4.3.2). Tracker dans backlog
   itération 3.

4. **Artifactory en dev sert des mock keys non-production.** Les clés
   distribuées proviennent de `mosip/artifactory-ref-impl` (publique, pré-générée).
   Aucune valeur cryptographique. En staging+ : artifactory dédié signé par
   le Lead crypto avec les clés Luna.

5. **[CLÔTURÉ ADR-R01 2026-05-25]** Risque d'origine : *espace d'IDs à length=9
   ≈ 16 millions vs population Sénégal 2026 ≈ 19,4 M — marge négative*. La
   décision Lead du 2026-05-25 a tranché en faveur de `length=10` (défaut
   MOSIP, ~164 M IDs effectifs, marge ~4 générations sur 50 ans). L'avenant
   TOR §4.1.3 est en cours de transmission au sponsor ANIU. Voir
   `architecture/sprint-2/ADR-REFACTOR-v1.md` ADR-R01 et
   `architecture/research/verhoeff/MOSIP-UIN-FEASIBILITY.md` pour le détail
   du sampling Monte-Carlo qui a démontré l'impossibilité technique du 9 chiffres.

6. **Schéma des `values` upstream non vérifié en intégral** pour artifactory
   et softhsm (réseau sandbox restreint). Les clés (`persistence.*`,
   `service.*`, `securityPIN`, etc.) sont **probables** au regard de
   l'architecture MOSIP standard. Le Lead doit dump avec `helm show values`
   (cf. §Procédure étape 3) et adapter si nécessaire. Si une clé ne matche
   pas, c'est silencieusement ignoré par Helm.

7. **Dépendances ENCORE manquantes (post-v2)** — à vérifier au prochain sync :
   - `conf-secrets` : chart MOSIP qui crée les Secrets requis par config-server
     (git creds, signing keys). README upstream config-server le liste comme
     prérequis. **PROBABLEMENT OK en dev** (repo mosip-config public, pas de
     creds requises) mais à valider.
   - `keycloak-init-mosip` : Job qui initialise le realm Keycloak MOSIP +
     les clients OAuth. Non requis MVP UIN (idgenerator bypassé via
     `proxy-otp=true`), mais sera requis dès qu'on attaque IDA en iter-2.
   - `postgres-init` : Job qui crée les schémas `mosip_kernel`,
     `mosip_keymgr`, `mosip_audit`. Le chart `idgenerator` est censé porter
     ses propres init-containers — à vérifier sur le premier sync.
   - Si l'un de ces 3 manque et bloque, ré-ouvrir un patch v3.

## Backlog — itération 2

| # | Item | Trigger |
|---|---|---|
| 1 | Ajouter charts `websub`, `masterdata-loader`, `kernel` complet | Quand on attaque l'enrôlement |
| 2 | Activer `kafka-mosip-cluster.yaml` (décommenter) | Avant websub |
| 3 | Ajouter chart `id-repository` | Pour stocker l'identité associée à l'IUN |
| 4 | Ajouter chart `id-authentication` (IDA) + `softhsm-ida` | Pour eKYC + OTP signé |
| 5 | Activer le Keycloak realm MOSIP (`keycloak-realm-mosip.yaml`) | Avant IDA |
| 6 | Ajouter chart `resident-services` + portail | Pour démo bout-en-bout citoyen |
| 7 | Fork `mosip-config` → `iun-sn-mosip-config` avec overrides Sénégal | Quand >10 overrides à porter |
| 8 | Route OpenShift TLS publique pour resident-services | Pour démo externe |
| 9 | Intégration 3scale (gateway API) | TOR §3.3.2 / §5.4 |
| 10 | **HSM Luna** (rip-out softhsm-kernel) | Avant staging — bloquant |
| 11 | Provider ABIS (TECH5 / Aware / Idemia) | TOR §3.3.3 |
| 12 | Artifactory dédié signé clés Luna (rip-out mock keys) | Avant staging — bloquant |

## Commit local (à exécuter par le Lead)

Le sandbox a pu écrire les fichiers mais n'a pas de creds git pour push.
Procédure côté Lead :

```powershell
Set-Location C:\IUN_APP\gitops

# 1. Si .git/index.lock existe d'une session précédente, le supprimer :
if (Test-Path .git\index.lock) { Remove-Item .git\index.lock -Force }

# 2. Vérifier que git status est cohérent avant d'ajouter :
git status --short apps/dpg/mosip/

# 3. Stager uniquement le sous-arbre MOSIP :
git add apps/dpg/mosip/

# 4. Vérifier le diff avant commit :
git diff --cached --stat apps/dpg/mosip/

# 5. Commit (message multi-ligne — copier-coller tel quel) :
git commit -m "feat(mosip): extend MVP chain with missing dependencies (global + artifactory + softhsm-kernel)" `
           -m "The minimal initial scaffold deployed config-server/keymanager/idgenerator only, but" `
           -m "those modules reference ConfigMaps 'global', 'artifactory-share', and 'softhsm-kernel-share'" `
           -m "in envFrom -- without the producing charts deployed, pods stayed in CreateContainerConfigError" `
           -m "for 3h47 on the first sync." `
           -m "Add 3 new Argo CD Applications with sync-wave 5/6/7 (before consumer modules at wave 10/20):" `
           -m "- mosip-global (manifest, not chart): cluster-wide MOSIP config (hostnames + HSM pins)" `
           -m "- mosip-artifactory (v12.0.4): artifact distribution (mock keys dev, ID schemas)" `
           -m "- mosip-softhsm-kernel (v12.0.4): software HSM for kernel module crypto (DEV ONLY)" `
           -m "Also rebumped existing waves: config-server 0->10, keymanager 10->20, idgenerator 20->20." `
           -m "Refs: TOR Sec.4.1.4, mosip-infra v1.2.0.x deployment order, k8s-infra global_configmap sample"

# 6. Push :
git push origin main
```

## Références

- TOR officielle v2.0 : `proposition_IUN_Senegal_vf.docx` §3.2 C2 + §4.1 + §4.3
- Décisions projet : `C:\IUN_APP\decisions\` + `C:\IUN_APP\architecture\`
- Doc MOSIP : <https://docs.mosip.io/1.2.0/>
- Repos MOSIP : <https://github.com/mosip>
- Helm charts MOSIP : <https://github.com/mosip/mosip-helm>
- Co