# components/iun-api/base — Placeholder

À remplir phase 2 — Deployment + Service + Route + ServiceMonitor de l'API .NET IUN.

## Prérequis avant remplissage

1. Containerfile .NET 8 dans `C:\IUN_APP\IUN.Api\`.
2. Pipeline Tekton de build/push vers Quay (Pipelines Operator déjà installé).
3. Decision sur le namespace cible : `iun-api` (à créer via `CreateNamespace=true`).

## Branchements ultérieurs

- AuthN/AuthZ via Keycloak realm IUN (cf. `../../keycloak-realm/`).
- Secrets via External Secrets Operator (déjà installé) + Vault.
- Connexion Postgres via Cluster CNPG IUN (cf. `../../cnpg-cluster/`).
