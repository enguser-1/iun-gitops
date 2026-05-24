# Chantiers obsolètes (pré-pivot DPG, 2026-05-24)

> Tracé des chantiers qui ont été engagés sous l'hypothèse d'une réécriture
> custom du démonstrateur `IUN.Api`, et qui sont devenus obsolètes après prise
> en compte de la TOR officielle v2.0 (`proposition_IUN_Senegal_vf.docx`).
>
> Conservé pour traçabilité du raisonnement et identification du know-how
> récupérable. Voir aussi `archive/README.md` et `C:\IUN_APP\architecture\REPENSEE-PROJET-v1.md`.

---

## 1. PoC #2 — verify 2000 RPS HMAC

**Localisation** : `C:\IUN_APP\poc2\`

**Objectif initial** : valider qu'un service Quarkus IUN-verify peut tenir 2 000 RPS
en validant des IUN par HMAC-SHA-256 + salt.

**Pourquoi obsolète** :
- L'algorithme de contrôle imposé par la TOR §4.1.3 / §4.1.4 est **Verhoeff pur** (théorie des
  groupes diédraux), pas HMAC. MOSIP gère cette vérification nativement via
  `mosip.kernel.idgenerator.uin.check-digit-algorithm = VERHOEFF`.
- La performance 2 000 RPS reste un objectif valide, mais sera mesurée côté **MOSIP
  ID Authentication** (TOR §4.3.1), pas sur un service IUN-verify custom.

**Récupérable** :
- Le harnais de charge (Gatling, k6 ou JMeter) reste réutilisable pour benchmarker
  MOSIP ID Authentication. La métrique cible passe de "2 000 RPS verify HMAC" à
  "TPS sustained sur `/v1/identity/auth/otp/validate`" (TOR §8.3 KPIs).
- La méthodologie de test (rampe, plateau, métriques p95/p99) reste valable.
- Les conclusions sur la limite de scale verticale d'un Quarkus mono-pod restent
  utiles comme baseline pour dimensionner MOSIP en staging.

**À faire** : archiver les rapports `poc2/` et reprendre la méthodologie pour
benchmarker MOSIP en phase pilote (TOR §5.5 P2).

---

## 2. Migration salt HMAC → Vault Transit

**Localisation** : `C:\IUN_APP\security\salt-migration\`

**Objectif initial** : déplacer le salt HMAC utilisé par le service IUN-verify
de variables d'environnement vers HashiCorp Vault Transit Encryption, avec
rotation automatique.

**Pourquoi obsolète** :
- Plus de HMAC → plus de salt à protéger. La TOR §4.1.2 prescrit un **hash SHA-256
  du IUN sous HSM Luna FIPS 140-2 Level 3** (TOR §4.3.2), géré par MOSIP keymanager.
- Le périmètre "protection des clés cryptographiques" passe sous la responsabilité
  de **MOSIP keymanager** + **HSM Luna**, pas d'une intégration Vault Transit
  custom.

**Récupérable** :
- L'**instance Vault** déjà installée sur le cluster (Vault Secrets Operator v1.4.0,
  cf. README racine §0 "Bonus repéré") reste utile pour :
  - Les secrets génériques des 3 DPG (mots de passe DB, tokens API partenaires)
  - L'intégration HSM Luna côté Vault (Vault HSM Enterprise — à arbitrer vs PKCS#11 direct dans MOSIP keymanager)
- Le know-how acquis sur Vault Operator + ESO reste mobilisable côté secrets DPG.
- La gouvernance des clés (rotation, audit, sealing) reste un sujet — porté
  désormais par la combinaison MOSIP keymanager + HSM Luna + Vault Secrets Operator.

**À faire** : reformuler le ticket "migration salt" en "gouvernance secrets DPG via Vault Secrets Operator" et l'attacher au lot d'intégration DPG.

---

## 3. Hello-Quarkus `iun-api` (PoC chaîne end-to-end)

**Localisation** : `C:\IUN_APP\iun-api\` (si existant) et Application Argo CD
`iun-api-hello` (déplacée vers `archive/iun/iun-api-hello.yaml`).

**Objectif initial** : valider la chaîne complète **GitOps Argo CD → repo Quarkus → image quay → déploiement OCP** sur un hello-world Quarkus, comme tremplin avant le portage `IUN.Api` (.NET) en Quarkus.

**Pourquoi obsolète** :
- Pas de **réécriture Quarkus prévue** (TOR §3.2 + §4 mandate les DPG).
- L'IUN n'est plus un service à coder, c'est une capacité MOSIP (TOR §4.1.4).

**Récupérable** :
- La **chaîne Argo CD ↔ repo applicatif externe** validée par ce PoC reste utile
  pour les futures Applications DPG qui suivent le même pattern (repo Helm/Kustomize externe → Application Argo CD dans `iun-gitops`).
- Le **pipeline Tekton** de build/push d'image (s'il a été créé) reste réutilisable
  pour la couche d'intégration FastAPI/Camel K (`apps/integration/`) qui nécessitera
  un build d'images custom (adapter NINA, adapter Orange Money, etc.).
- L'expérience sur **OpenShift Routes + Service Mesh** reste pertinente — les 3 DPG
  exposeront aussi des Routes (PWA OpenCRVS, portails MOSIP, console OpenIMIS).
- L'instance Argo CD dédiée (`iun-argocd` dans `iun-gitops`) et le module
  `IunOc.psm1` (`Invoke-Oc` / `Get-OcJson` / `Test-OcAccess`) restent **strictement réutilisés**.

**À faire** : conserver le know-how pipeline Tekton pour `apps/integration/`. Aucune action immédiate sur `C:\IUN_APP\iun-api\` (peut être supprimé après confirmation du Lead).

---

## 4. ADR-001 — Stack technique Quarkus + CNPG + Istio + Kafka

**Localisation** : `C:\IUN_APP\architecture\decisions\ADR-001-stack-technique.md`

**Statut** : **partiellement obsolète**. Décomposition :

### Sections de l'ADR-001 qui restent valides

(Le socle transverse est confirmé à l'identique par TOR §5.4 — pas besoin de
réécrire ces décisions.)

| Section ADR-001 | Statut | Référence TOR |
|---|---|---|
| OCP 4.x comme socle | ✅ valide | TOR §3.1, §5 |
| OpenShift Service Mesh (Istio) — mTLS bout en bout | ✅ valide | TOR §3.3.1 |
| AMQ Streams (Kafka) — notifications inter-systèmes | ✅ valide | TOR §3.4.2 |
| RHBK (Red Hat Build of Keycloak) — IAM transverse | ✅ valide | TOR §5.4 |
| ODF (OpenShift Data Foundation) — stockage | ✅ valide | TOR §5.4 |
| Vault + ESO — gestion des secrets | ✅ valide | TOR §5.4 (implicite) |
| OpenShift Logging (Loki + Vector) — observabilité | ✅ valide | TOR §5.4, §5.2 |
| OpenShift GitOps (Argo CD) — déploiement déclaratif | ✅ valide | TOR §5.4 |
| OpenShift Compliance Operator — FIPS + STIG + CIS | ✅ valide | TOR §3.3.4, §5.4, §6.3 |
| OpenShift Pipelines (Tekton) — CI/CD | ✅ valide | (implicite — utilisé pour intégrations custom) |

### Sections de l'ADR-001 archivées

| Section ADR-001 | Statut | Raison |
|---|---|---|
| Runtime Quarkus pour `iun-api` | ❌ archivée | Pas de réécriture — MOSIP+OpenCRVS+OpenIMIS sont en Java/Node/Python upstream |
| CloudNativePG dédié IUN (cluster `iun-postgres`) | ❌ archivée sous cette forme | Chaque DPG embarque son propre Postgres (via chart upstream). CNPG reste utilisé mais provisionné par les DPG, pas en cluster transverse "iun-postgres". |
| Schéma de données IUN custom | ❌ archivée | Schémas MOSIP + OpenCRVS (FHIR R4) + OpenIMIS pris tels quels |
| API REST OpenAPI custom pour verify IUN | ❌ archivée | MOSIP ID Authentication expose ce contrat |
| Compliance ISO 27001 application-level | ↻ déplacée | Devient compliance plateforme DPG (TOR §6.3, certification M24) |

**À faire** : créer un **ADR-002** dans `architecture/decisions/` qui :
1. Recense le périmètre validé d'ADR-001 (socle confirmé).
2. Documente l'archivage des sections runtime applicatif.
3. Ouvre les arbitrages restants : 3scale on-prem vs SaaS, ABIS provider, FastAPI vs Camel K, etc.

---

## 5. Synthèse — ce qui est globalement récupérable

- ✅ **L'instance Argo CD dédiée** `iun-argocd` dans `iun-gitops` — outil de travail principal, conservé tel quel.
- ✅ **Le module PowerShell `IunOc.psm1`** (`Invoke-Oc` / `Get-OcJson` / `Test-OcAccess`) — règle PS 5.1 quoting essentielle.
- ✅ **Les scripts d'amorçage** : `Bootstrap-IUN.ps1`, `Get-ArgoCD-Admin.ps1`, `Test-IUNCluster.ps1`.
- ✅ **Le rapport `EVALUATION_OPENSHIFT_v4.16.md`** : 100 % valide sur le socle (les 11 Operators), à relativiser sur la couche applicative.
- ✅ **L'analyse `INTEGRATION-SYSTEMES-EXISTANTS-v1.md`** (guide Accel Tech FastAPI) : reconnue comme la couche d'intégration légère DPG ↔ systèmes nationaux.
- ✅ **La note de souveraineté** : reste valide (ADIE, RHCOS FIPS, EAL4+, etc.).
- ✅ **Le know-how réseau / OCP / DevSecOps** acquis pendant le Sprint 0.

- ❌ **Le runtime applicatif Quarkus** custom pour IUN — non applicable.
- ❌ **Le PoC HMAC + salt** — non applicable (Verhoeff suffit + hash sous HSM).
- ❌ **Le harness verify 2 000 RPS HMAC** — réutilisable méthodologiquement mais à recentrer sur MOSIP ID Authentication.

---

*Document maintenu par le Lead Platform Engineer + Architecte DPG. À mettre à jour
quand un nouveau chantier devient obsolète ou quand un chantier obsolète se révèle
finalement réutilisable.*
