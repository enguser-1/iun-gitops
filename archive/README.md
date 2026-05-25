# archive/ — Artefacts pré-pivot DPG (2026-05-24)

> **Statut** : non utilisé. Conservé pour traçabilité uniquement.
>
> **NOTE 2026-05-25** : ce document conserve la mention historique d'un IUN
> « 9 chiffres + Verhoeff » telle qu'elle figurait dans la TOR v2.0 au moment
> du pivot DPG du 2026-05-24. Cette spécification a été révisée le 2026-05-25
> par l'ADR-R01 (`architecture/sprint-2/ADR-REFACTOR-v1.md`) qui acte le format
> définitif **10 chiffres** dont le 10ᵉ est un check digit Verhoeff. Le contenu
> ci-dessous est conservé en l'état pour traçabilité du pivot.

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
- Module `IunOc.psm1` (`Invoke-Oc` / `Get-OcJson` 