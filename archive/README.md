# archive/ — Artefacts pré-pivot DPG (2026-05-24)

> **Statut** : non utilisé. Conservé pour traçabilité uniquement.

## Contexte

Ces artefacts ont été produits sous l'hypothèse d'une **réécriture custom Quarkus**
du démonstrateur `IUN.Api` (.NET → Java/Quarkus) **avant la prise en compte
de la TOR officielle v2.0** (`proposition_IUN_Senegal_vf.docx`, avril 2026).

La TOR §3.2 et §4 mandate en réalité le déploiement, sur OCP 4.x, de **trois
Digital Public Goods** :

- **OpenCRVS** — état civil (TOR §3.2 C1, §4.2)
- **MOSIP** — génération IUN 9 chiffres + Verhoeff (TOR §3.2 C2, §4.1, §4.3)
- **OpenIMIS** — protection sociale CMU/PNBSF/IPRES/CSS (TOR §3.2 C3, §4.4)

L'IUN n'est donc **pas** un service à coder — c'est une **capacité native de
MOSIP** activée par configuration (`mosip.kernel.idgenerator.uin.check-digit-algorithm=VERHOEFF`).

Voir `C:\IUN_APP\architecture\REPENSEE-PROJET-v1.md` pour le rationalé complet
du pivot stratégique et le plan 30 jours associé.

## Contenu archivé

| Chemin | Origine | Statut |
|---|---|---|
| `iun/` | `apps/iun/` — aggregator Kustomize Approche B + Application Argo CD `iun-api-hello` | Obsolète — remplacé par `apps/dpg/` |
| `iun-api/` | `apps/iun-api/` — README legacy de l'ancien layout | Obsolète |
| `components/iun-api/base/` | Placeholder Deployment/Service/Route API .NET | Obsolète — pas de réécriture Quarkus |
| `components/cnpg-cluster/base/` | Placeholder Cluster PostgreSQL IUN dédié | Obsolète — Postgres est géré par MOSIP/OpenCRVS/OpenIMIS via leurs charts upstream |
| `components/keycloak-realm/base/` | Placeholder Realm Keycloak IUN | Obsolète sous cette forme — MOSIP embarque son propre Keycloak (à arbitrer : on harmonise via RHBK existant ou on reste sur celui de MOSIP) |
| `components/kafka-cluster/base/` | Placeholder Cluster Kafka IUN | Obsolète sous cette forme — la TOR §3.4.2 confirme un usage Kafka via AMQ Streams existant, mais piloté par les sous-systèmes DPG (MOSIP kafka topics, OpenCRVS notifications) |

## Ce qui reste valide (non archivé)

- `bootstrap/` : l'instance Argo CD dédiée `iun-argocd` reste l'orchestrateur GitOps des 3 DPG. Aucun changement de scope.
- `environments/{dev,staging,prod}/` : conserve la séparation par environnement, repointe désormais sur `apps/dpg/` (à mettre à jour).
- `Bootstrap-IUN.ps1` / `Get-ArgoCD-Admin.ps1` / `Test-IUNCluster.ps1` / `IunOc.psm1` / `Test-OcWrapper.ps1` : scripts wrappers PowerShell — strictement réutilisés.
- Module `IunOc.psm1` (`Invoke-Oc` / `Get-OcJson` / `Test-OcAccess`) : règle PS 5.1 quoting à maintenir (voir mémoire `[[powershell-oc-quoting]]`).

## Pour creuser

- Décisions de socle qui restent valides : voir TOR §5.4 (Service Mesh, Vault, AMQ Streams, RHBK, ODF, Compliance Operator, GitOps, Logging — confirmés à l'identique).
- Chantiers techniques rendus obsolètes par le pivot : voir `CHANTIERS-OBSOLETES.md` à côté.

---
*Pivot acté le 2026-05-24 par le Lead Platform Engineer + Architecte DPG.*
