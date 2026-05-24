#Requires -Version 5.1
<#
.SYNOPSIS
  Commit local (sans push) de la livraison OpenIMIS iter 1 (option A).
.DESCRIPTION
  Le commit n'a pas pu être exécuté depuis le sandbox Linux de l'agent
  (le dossier .git/ du dépôt monté est verrouillé par Windows : opération
  refusée sur _junk_*, _idx_*, index.lock). Ce script reproduit
  exactement les commandes attendues par le prompt.
.NOTES
  Fichier UTF-8 avec BOM (PS 5.1 friendly). À exécuter depuis Windows.
#>
[CmdletBinding()]
param(
  [string]$RepoRoot = 'C:\IUN_APP\gitops'
)

$ErrorActionPreference = 'Stop'
Set-Location $RepoRoot

# Sanity check
& git rev-parse --is-inside-work-tree | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Not a git working tree: $RepoRoot" }

Write-Host "[1/3] Stage apps/dpg/openimis/ ..." -ForegroundColor Cyan
& git add 'apps/dpg/openimis/'
if ($LASTEXITCODE -ne 0) { throw "git add failed" }

Write-Host "[2/3] Diff résumé :" -ForegroundColor Cyan
& git diff --cached --stat -- apps/dpg/openimis/

Write-Host "[3/3] Commit (sans push) ..." -ForegroundColor Cyan
$msg = @'
feat(openimis): first deployable iteration — option A chosen

- Option A selected for K8s packaging (no upstream Helm chart available);
  forks (option B) absent of any maintained Helm candidate as of 2026-05-24;
  upstream contribution (option C) scheduled as track 2.
- Dev manifests cleaned and OCP-adapted: SCC nonroot-v2, drop ALL caps,
  allowPrivilegeEscalation=false, runAsNonRoot, Route OCP instead of
  Traefik labels, PVC RWX (cephfs) for shared photos, healthchecks K8s
  natifs, Secrets externalisés (PoC values, à migrer vers ESO iter 2).
- ConfigMap with SN social protection programmes (CMU, PNBSF, IPRES, CSS)
  + 14 regions ANSD + intégrations MOSIP/OpenCRVS désactivées par défaut.
- MVP scope: create family + policy + claim flow on openimis-be 25.10 +
  openimis-fe 25.10 + openimis-pgsql 25.10 + redis 7 + rabbitmq 3.13 +
  opensearch 2.13 (+ dashboards). Integration with MOSIP/OpenCRVS
  deferred to iter 2 (apps/integration/).
- Risks documented in README §8: option A longevity, upstream Helm absent,
  OpenSearch vm.max_map_count, NGINX port-binding under SCC restricted.
'@
& git commit -m $msg
if ($LASTEXITCODE -ne 0) { throw "git commit failed" }

Write-Host "`n--- HEAD ---" -ForegroundColor Green
& git log -1 --stat
Write-Host "`nDONE — local commit only. À pousser : git push origin main" -ForegroundColor Yellow
