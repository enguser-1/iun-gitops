# MOSIP — Modular Open Source Identity Platform

## Rôle dans IUN Sénégal

MOSIP est **le cœur identitaire** du programme IUN. Référence TOR :

- **§3.2 C2** — Composant 2 : système d'identification (MOSIP)
- **§4.1.2** — Génération **IUN 9 chiffres** (8 aléatoires + 1 Verhoeff), affichage `SN-NNNN-NNNN-N`
- **§4.1.3 / §4.1.4** — Algorithme Verhoeff (théorie des groupes diédraux), activé par
  `mosip.kernel.idgenerator.uin.check-digit-algorithm = VERHOEFF`
- **§4.3.1** — ID Authentication (OTP signé, eKYC, audit immuable)
- **§4.3.2** — Stockage SHA-256 sous HSM Luna FIPS 140-2 L3, biométries chiffrées client-side (RSA-2048 + AES-256)

> **Point critique** : l'IUN **n'est pas un service que l'on code**. C'est une **capacité native
> de MOSIP** activée par configuration. Le démonstrateur `IUN.Api` (.NET) **n'est pas réécrit en
> Quarkus** — il est *remplacé* par MOSIP UIN Generator + ID Repository + ID Authentication.

## Source officielle

| Élément | Valeur | Notes |
|---|---|---|
| Helm repo | `https://mosip.github.io/mosip-helm` | Repo officiel `mosip/mosip-helm` |
| Mode de déploiement | **V3** (Kubernetes/Helm) | Supporté depuis 1.2.0.1-B1 |
| Version visée | **1.2.1.0** (LTS) | À reconfirmer au moment du déploiement applicatif — voir `community.mosip.io` pour MAJ |
| Config repo | `https://github.com/mosip/mosip-config` | Référentiel des ConfigMap kernel — à cloner pour valider clés |
| Infra repo | `https://github.com/mosip/mosip-infra` | Scripts d'amorçage et docs |
| Doc | `https://docs.mosip.io/1.2.0/` | Référence opérationnelle |

## Composants déployés (≈7 modules majeurs)

MOSIP est **modulaire** — la trajectoire est de déployer module par module, pas en un coup :

| Phase | Module | Rôle |
|---|---|---|
| 1 — Bootstrap | `mosip-monitoring` (probe) | Validation de la chaîne Argo CD ↔ Helm MOSIP |
| 2 — Kernel | `kernel-config`, `kernel-uin-generator`, `kernel-cryptomanager`, `kernel-keymanager` | UIN/IUN + crypto + clés maîtresses |
| 3 — Données | `id-repository`, `id-authentication` | Persistance identité + authent |
| 4 — Enrôlement | `pre-registration`, `registration-processor`, `registration-client` | Enrôlement citoyen |
| 5 — Front-office | `resident-services`, `partner-management`, `admin-services` | Portails citoyen + partenaire + admin |
| 6 — Biométrie | ABIS provider (tech5/aware/idemia) | Déduplication biométrique (TOR §3.3.3) |
| 7 — Périphérique | Tablet Android MOSIP + Red Hat Device Edge (MicroShift) | Unités mobiles (TOR §5.6.2) |

Chaque phase ouvrira ses propres `Application` Argo CD dans ce dossier.

## Personnalisations Sénégal

Différences vs valeurs par défaut MOSIP, documentées dans `values/dev.yaml` :

- **`length: 9` + `checkDigitAlgorithm: VERHOEFF`** sur le UIN generator (forme `NNNNNNNNN`, affichée `SN-NNNN-NNNN-N`).
- **`initialPoolSize`** ramené à 1000 en dev (vs 1M en prod) — pré-génération d'UINs.
- **`kafka.external: true`** : on consomme l'AMQ Streams déjà installé sur le cluster plutôt que le Kafka embarqué.
- **`postgres.external: true`** : on consomme CNPG cluster-wide.
- **`ingress.enabled: false`** : on exposera via **OpenShift Route** (et 3scale en prod — TOR §3.3.2).
- **`hsm.enabled` / `abis.enabled`** : faux en dev sandbox, vrai à partir de staging.

> ConfigMap d'override : `manifests/configmap-iun-format.yaml`. **Clés à VALIDER**
> contre `mosip/mosip-config @ release-1.2.1.0` avant le premier déploiement applicatif.

## Prérequis cluster

> **Réalité MOSIP** : MOSIP est **lourd**. Le profil sandbox demande déjà ~50 vCPU et 100 Gi RAM.
> Pour le profil production (TOR §5.5 P1, 50k TPS), c'est plusieurs centaines de vCPU.

| Ressource | Dev (sandbox) | Staging (iso-prod réduit) | Prod (target) |
|---|---|---|---|
| vCPU réservés | ~50 | ~150 | ~500 |
| RAM réservée | ~100 Gi | ~300 Gi | ~1 Ti |
| Stockage ODF | ~200 Gi | ~1 Ti | ~10 Ti |
| Operators OCP | CNPG, AMQ Streams, RHBK, ODF, Logging, cert-manager, GitOps | + ESO + Vault | + Compliance + 3scale + RHACM |

## Procédure de déploiement (commandes PowerShell)

```powershell
# 1. Préchecks (état cluster partagé)
Set-Location C:\IUN_APP\gitops
.\Test-IUNCluster.ps1

# 2. Bootstrap Argo CD (déjà fait si Bootstrap-IUN.ps1 a été lancé)
.\Bootstrap-IUN.ps1 -Environment dev

# 3. Forcer un refresh / sync manuel de la Application MOSIP probe
Import-Module .\IunOc.psm1
Invoke-Oc annotate application mosip-bootstrap-probe -n iun-gitops `
  argocd.argoproj.io/refresh=hard --overwrite

# 4. Suivre le statut
Get-OcJson get application mosip-bootstrap-probe -n iun-gitops |
  Select-Object -ExpandProperty status |
  Select-Object sync, health

# 5. Récupérer la liste des pods quand la Application sera sync OK
Invoke-Oc get pods -n mosip-dev
```

## Validation post-déploiement

- Pods `mosip-monitoring-*` en `Running` / `Ready 1/1` dans `mosip-dev`
- Application Argo CD en `Synced` / `Healthy`
- ConfigMap `iun-format-override` présente : `oc get cm iun-format-override -n mosip-dev -o yaml`
- (Phase 2 kernel) Endpoint UIN Generator répond : `POST /v1/idgenerator/uin` retourne un IUN à 9 chiffres dont le 9ᵉ est un check-digit Verhoeff valide.

## Backlog / TODO

- **Valider les clés ConfigMap** contre `mosip/mosip-config @ 1.2.1.0` (clé canonique pour `length` et `check-digit-algorithm`).
- **Choisir ABIS provider** : TECH5 vs Aware vs Idemia (TOR §3.3.3, taux faux positif < 0,001 %).
- **Arbitrer Keycloak embarqué vs RHBK existant** : MOSIP embarque son propre Keycloak. Le cluster a déjà RHBK Operator installé. Décision attendue — coût/bénéfice à mesurer.
- **3scale** (TOR §3.3.2, §5.4) : intégration prévue côté `apps/integration/`. Pas dans ce dossier.
- **HSM Luna FIPS 140-2 L3** (TOR §3.3.3, §4.3.2) : intégration via Vault HSM ou direct PKCS#11. Pas dans le scope de cette Application probe.
- **Resident Services / Partner Management** : pas dans le scope de la bootstrap-probe. Phase 5 ci-dessus.
- **Unités mobiles + MicroShift** (TOR §5.6.2) : hors scope cluster central — séparé.

## Références

- TOR officielle v2.0 : `proposition_IUN_Senegal_vf.docx`
- Document stratégique de pivot : `C:\IUN_APP\architecture\REPENSEE-PROJET-v1.md`
- Doc MOSIP : https://docs.mosip.io/1.2.0/
- Helm charts MOSIP : https://github.com/mosip/mosip-helm
