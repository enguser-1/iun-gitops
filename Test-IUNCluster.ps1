<#
.SYNOPSIS
    Précheck cluster OpenShift avant le bootstrap GitOps IUN.
    Produit un rapport markdown court (lisible en console et sur disque).

.DESCRIPTION
    Test-IUNCluster.ps1 inventorie en lecture seule l'état du cluster sur les points
    structurants pour le déploiement du socle IUN :
        - Connexion oc + utilisateur courant + version OCP
        - Privilèges cluster-admin (oc auth can-i)
        - Présence du CSV OpenShift GitOps Operator
        - État de l'instance Argo CD partagée (openshift-gitops) — diagnostic
        - Présence du namespace cible iun-gitops
        - Présence du namespace iun-sandbox (sandbox Lead)
        - Disponibilité des CRDs Argo CD (applications.argoproj.io, argocds.argoproj.io)
        - Operators socle déjà installés (parmi les 11 du §4 du rapport eval)
        - Quotas / nb de projects (sanity sur cluster partagé)

    Le rapport markdown est :
        - Affiché sur stdout
        - Écrit dans C:\IUN_APP\gitops\.logs\precheck-YYYYMMDD-HHMMSS.md (sauf -ReportPath)
        - Renvoyé en code retour : 0 = OK (ou warnings), 1 = bloquant

.PARAMETER ReportPath
    Chemin du rapport markdown généré.
    Défaut : C:\IUN_APP\gitops\.logs\precheck-YYYYMMDD-HHMMSS.md

.PARAMETER SkipTlsVerify
    Ajoute --insecure-skip-tls-verify=true à oc. Défaut : $true.

.PARAMETER NoFile
    N'écrit pas le rapport sur disque (uniquement console).

.EXAMPLE
    PS> .\Test-IUNCluster.ps1
    Précheck complet + rapport horodaté dans .logs\.

.EXAMPLE
    PS> .\Test-IUNCluster.ps1 -ReportPath C:\IUN_APP\reports\precheck-J0.md

.NOTES
    Read-only : ne modifie jamais le cluster.
    Utilisé en J0 du chantier et avant chaque rejouage de Bootstrap-IUN.ps1.
#>

[CmdletBinding()]
param(
    [string] $ReportPath,
    [switch] $SkipTlsVerify = $true,
    [switch] $NoFile
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Get-OcFlags {
    if ($SkipTlsVerify) { return @('--insecure-skip-tls-verify=true') }
    return @()
}

function Invoke-OcQuiet {
    param([Parameter(Mandatory)] [string[]] $OcArgs)
    $full   = @('oc') + (Get-OcFlags) + $OcArgs
    $output = & $full[0] $full[1..($full.Count - 1)] 2>&1
    return [pscustomobject]@{
        Code   = $LASTEXITCODE
        Output = ($output | Out-String).TrimEnd()
    }
}

$report  = [System.Collections.Generic.List[string]]::new()
$summary = [System.Collections.Generic.List[pscustomobject]]::new()

function Add-Section { param([string] $Title) $report.Add(""); $report.Add("## $Title"); $report.Add("") }
function Add-Line    { param([string] $Line) $report.Add($Line) }
function Add-Check {
    param([string] $Name, [ValidateSet('OK','WARN','FAIL','INFO')] [string] $Status, [string] $Detail = '')
    $emoji = switch ($Status) {
        'OK'   { '[OK]   ' }
        'WARN' { '[WARN] ' }
        'FAIL' { '[FAIL] ' }
        'INFO' { '[INFO] ' }
    }
    $line = "- $emoji **$Name**"
    if ($Detail) { $line += " — $Detail" }
    $report.Add($line)
    $summary.Add([pscustomobject]@{ Name = $Name; Status = $Status; Detail = $Detail })
}

# ---------------------------------------------------------------------------
# En-tête
# ---------------------------------------------------------------------------
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$report.Add("# Précheck cluster — IUN")
$report.Add("")
$report.Add("- Date        : $ts")
$report.Add("- Hôte exec   : $env:COMPUTERNAME")
$report.Add("- Utilisateur : $env:USERNAME")

# ---------------------------------------------------------------------------
# Connexion & contexte
# ---------------------------------------------------------------------------
Add-Section "Connexion et contexte"

if (-not (Get-Command oc -ErrorAction SilentlyContinue)) {
    Add-Check "oc dans le PATH" "FAIL" "binaire introuvable"
    $report -join "`n" | Write-Host
    exit 1
}
Add-Check "oc dans le PATH" "OK" (Get-Command oc).Source

$whoami = Invoke-OcQuiet -OcArgs @('whoami')
if ($whoami.Code -ne 0) {
    Add-Check "Session oc" "FAIL" "oc whoami a échoué — fais 'oc login' d'abord"
    $report -join "`n" | Write-Host
    exit 1
}
Add-Check "Session oc" "OK" "user = $($whoami.Output)"

$srv = Invoke-OcQuiet -OcArgs @('whoami','--show-server')
if ($srv.Code -eq 0) { Add-Check "Server actif" "INFO" $srv.Output }

$ver = Invoke-OcQuiet -OcArgs @('version','-o','json')
if ($ver.Code -eq 0) {
    try {
        $j = $ver.Output | ConvertFrom-Json
        Add-Check "Version OCP" "INFO" ("server=$($j.openshiftVersion) ; client=$($j.clientVersion.gitVersion)")
    } catch { Add-Check "Version OCP" "WARN" "parsing JSON KO" }
}

# ---------------------------------------------------------------------------
# Privilèges
# ---------------------------------------------------------------------------
Add-Section "Privilèges"

$admin = Invoke-OcQuiet -OcArgs @('auth','can-i','*','*','--all-namespaces')
if ($admin.Code -eq 0 -and $admin.Output.Trim().ToLower() -eq 'yes') {
    Add-Check "cluster-admin" "OK" "oc auth can-i * * --all-namespaces = yes"
} else {
    Add-Check "cluster-admin" "WARN" "non détecté — l'installation des Operators sera bloquée"
}

# ---------------------------------------------------------------------------
# Namespaces critiques
# ---------------------------------------------------------------------------
Add-Section "Namespaces critiques"

foreach ($ns in @('openshift-operators','openshift-gitops','iun-gitops','iun-sandbox','openshift-marketplace')) {
    $r = Invoke-OcQuiet -OcArgs @('get','namespace',$ns,'--ignore-not-found','-o','name')
    if ($r.Code -eq 0 -and $r.Output) {
        Add-Check "Namespace $ns" "OK" "présent"
    } elseif ($r.Code -eq 0) {
        # Distinguer iun-gitops (à créer par bootstrap) du reste
        $sev = if ($ns -eq 'iun-gitops') { 'INFO' } else { 'WARN' }
        Add-Check "Namespace $ns" $sev "absent"
    } else {
        Add-Check "Namespace $ns" "WARN" "lecture KO : $($r.Output)"
    }
}

# Nombre total de projects (sanity cluster partagé)
$projCount = Invoke-OcQuiet -OcArgs @('get','projects','--no-headers')
if ($projCount.Code -eq 0) {
    $n = ($projCount.Output -split "`n").Count
    Add-Check "Nb total de projects visibles" "INFO" "$n"
}

# ---------------------------------------------------------------------------
# CRDs Argo CD
# ---------------------------------------------------------------------------
Add-Section "CRDs Argo CD"

foreach ($crd in @('applications.argoproj.io','argocds.argoproj.io','appprojects.argoproj.io')) {
    $r = Invoke-OcQuiet -OcArgs @('get','crd',$crd,'--ignore-not-found','-o','name')
    if ($r.Code -eq 0 -and $r.Output) {
        Add-Check "CRD $crd" "OK" "présent"
    } else {
        Add-Check "CRD $crd" "FAIL" "absent — OpenShift GitOps Operator pas installé ?"
    }
}

# ---------------------------------------------------------------------------
# OpenShift GitOps Operator
# ---------------------------------------------------------------------------
Add-Section "OpenShift GitOps Operator"

$csv = Invoke-OcQuiet -OcArgs @('get','csv','-n','openshift-operators','-o','custom-columns=NAME:.metadata.name,PHASE:.status.phase','--no-headers')
if ($csv.Code -eq 0) {
    $gitopsLines = $csv.Output -split "`n" | Where-Object { $_ -match 'gitops' }
    if ($gitopsLines) {
        foreach ($l in $gitopsLines) {
            $status = if ($l -match 'Succeeded') { 'OK' } else { 'WARN' }
            Add-Check "CSV $($l.Trim())" $status ""
        }
    } else {
        Add-Check "CSV openshift-gitops-operator" "FAIL" "aucun CSV gitops dans openshift-operators"
    }
} else {
    Add-Check "Inventaire CSV" "WARN" "oc get csv KO : $($csv.Output)"
}

# État instance Argo CD partagée (diag uniquement)
$argoSharedNs = Invoke-OcQuiet -OcArgs @('get','namespace','openshift-gitops','--ignore-not-found','-o','name')
if ($argoSharedNs.Code -eq 0 -and $argoSharedNs.Output) {
    $pods = Invoke-OcQuiet -OcArgs @('get','pods','-n','openshift-gitops','--no-headers')
    if ($pods.Code -eq 0) {
        $running = ($pods.Output -split "`n" | Where-Object { $_ -match 'Running' }).Count
        $total   = ($pods.Output -split "`n").Count
        Add-Check "Pods instance Argo CD partagée" "INFO" "$running/$total Running dans openshift-gitops"
    }
}

# ---------------------------------------------------------------------------
# Operators socle déjà installés (les 11 du §4)
# ---------------------------------------------------------------------------
Add-Section "Operators socle (sur les 11 cibles)"

# Patterns appliqués UNIQUEMENT sur la colonne NAME du CSV (pas sur le namespace),
# sinon faux positifs sur cluster partagé (ex: namespace `cert-manager-operator`
# matche le pattern `cert-manager` même quand le CSV n'est pas présent).
$socleCheck = @(
    @{ Name = 'OpenShift Pipelines';      Pattern = 'openshift-pipelines-operator-rh' },
    @{ Name = 'OpenShift Service Mesh 3'; Pattern = 'servicemeshoperator3' },
    @{ Name = 'Red Hat Build of Keycloak';Pattern = 'rhbk-operator' },
    @{ Name = 'CloudNativePG';            Pattern = 'cloudnative-pg' },
    @{ Name = 'AMQ Streams';              Pattern = 'amqstreams' },
    @{ Name = 'OpenShift Logging';        Pattern = 'cluster-logging' },
    @{ Name = 'OpenShift Data Foundation';Pattern = 'odf-operator' },
    @{ Name = 'cert-manager';             Pattern = 'cert-manager-operator|^cert-manager\.v' },
    @{ Name = 'External Secrets';         Pattern = 'external-secrets-operator|^external-secrets\.v' },
    @{ Name = 'RHACS';                    Pattern = 'rhacs-operator|advanced-cluster-security' }
)

# Approche B : on parse `oc get csv -A` en jsonpath pour matcher proprement la
# colonne NAME. Format de chaque ligne : "<namespace>\t<csv-name>\t<phase>".
$csvJp  = '{range .items[*]}{.metadata.namespace}{"\t"}{.metadata.name}{"\t"}{.status.phase}{"\n"}{end}'
$csvAll = Invoke-OcQuiet -OcArgs @('get','csv','-A','-o',"jsonpath=$csvJp")
if ($csvAll.Code -eq 0) {
    $csvLines = $csvAll.Output -split "`n" |
        Where-Object { $_ -and (($_ -split "`t").Count -ge 2) }

    foreach ($op in $socleCheck) {
        $hit = $csvLines |
            Where-Object {
                $cols = $_ -split "`t"
                $cols[1] -match $op.Pattern
            } |
            Select-Object -First 1

        if ($hit) {
            $cols   = $hit -split "`t"
            $ns     = $cols[0]
            $name   = $cols[1]
            $phase  = if ($cols.Count -ge 3) { $cols[2] } else { '' }
            $detail = "$name ($ns) — $phase"
            $status = if ($phase -eq 'Succeeded') { 'OK' } else { 'WARN' }
            Add-Check $op.Name $status $detail
        } else {
            Add-Check $op.Name "INFO" "non installé"
        }
    }
} else {
    Add-Check "Inventaire CSV cluster-wide" "WARN" "oc get csv -A KO : $($csvAll.Output)"
}

# ---------------------------------------------------------------------------
# Synthèse
# ---------------------------------------------------------------------------
Add-Section "Synthèse"

$nFail = ($summary | Where-Object { $_.Status -eq 'FAIL' }).Count
$nWarn = ($summary | Where-Object { $_.Status -eq 'WARN' }).Count
$nOk   = ($summary | Where-Object { $_.Status -eq 'OK'   }).Count
$nInfo = ($summary | Where-Object { $_.Status -eq 'INFO' }).Count

Add-Line ("- OK    : {0}" -f $nOk)
Add-Line ("- WARN  : {0}" -f $nWarn)
Add-Line ("- FAIL  : {0}" -f $nFail)
Add-Line ("- INFO  : {0}" -f $nInfo)
Add-Line ""

if ($nFail -gt 0) {
    Add-Line "**Verdict : bloquant.** Corrige les FAIL avant de lancer Bootstrap-IUN.ps1."
} elseif ($nWarn -gt 0) {
    Add-Line "**Verdict : warnings.** Lis chaque WARN et confirme qu'il est attendu, puis go bootstrap."
} else {
    Add-Line "**Verdict : OK.** Cluster prêt pour Bootstrap-IUN.ps1."
}

# ---------------------------------------------------------------------------
# Sortie : console + fichier
# ---------------------------------------------------------------------------
$content = $report -join "`n"
Write-Host ""
Write-Host $content
Write-Host ""

if (-not $NoFile) {
    if (-not $ReportPath) {
        $logDir = 'C:\IUN_APP\gitops\.logs'
        if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        $ReportPath = Join-Path $logDir ("precheck-{0}.md" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    }
    Set-Content -Path $ReportPath -Value $content -Encoding UTF8
    Write-Host "Rapport écrit : $ReportPath" -ForegroundColor Cyan
}

if ($nFail -gt 0) { exit 1 } else { exit 0 }
