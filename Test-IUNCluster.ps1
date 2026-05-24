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

    Tous les appels oc passent par le module IunOc.psm1 (wrappers robustes
    Windows PowerShell 5.1, cf. Bootstrap-IUN.ps1).

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
# Import du module wrapper oc
# ---------------------------------------------------------------------------
$ModulePath = Join-Path $PSScriptRoot 'IunOc.psm1'
if (-not (Test-Path $ModulePath)) {
    Write-Error "Module IunOc.psm1 introuvable à côté du script ($ModulePath)."
    exit 1
}
Import-Module $ModulePath -Force -DisableNameChecking

if ($SkipTlsVerify) {
    Set-OcGlobalFlags -Flags @('--insecure-skip-tls-verify=true')
} else {
    Set-OcGlobalFlags -Flags @()
}

# ---------------------------------------------------------------------------
# Helpers locaux (réutilisent les wrappers du module)
# ---------------------------------------------------------------------------
function Invoke-OcQuiet {
    <#
        Adaptateur historique pour le reste du script. Renvoie un objet
        { Code, Output } compatible avec l'ancienne API.
    #>
    param([Parameter(Mandatory)] [string[]] $OcArgs)
    $r = Invoke-Oc -OcArgs $OcArgs -AllowFailure
    $out = if ($r.Stdout) { $r.Stdout } elseif ($r.Stderr) { $r.Stderr } else { '' }
    return [pscustomobject]@{
        Code   = $r.ExitCode
        Output = $out
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

try {
    $verJson = Get-OcJson -OcArgs @('version') -TimeoutSeconds 30
    if ($verJson.openshiftVersion) {
        $client = if ($verJson.clientVersion -and $verJson.clientVersion.gitVersion) { $verJson.clientVersion.gitVersion } else { 'n/a' }
        Add-Check "Version OCP" "INFO" ("server=$($verJson.openshiftVersion) ; client=$client")
    } else {
        Add-Check "Version OCP" "WARN" "openshiftVersion absent du JSON"
    }
} catch {
    Add-Check "Version OCP" "WARN" "oc version -o json KO : $($_.Exception.Message)"
}

# ---------------------------------------------------------------------------
# Privilèges
# ---------------------------------------------------------------------------
Add-Section "Privilèges"

if (Test-OcAccess -Verb '*' -Resource '*' -AllNamespaces) {
    Add-Check "cluster-admin" "OK" "oc auth can-i * * --all-namespaces = yes"
} else {
    Add-Check "cluster-admin" "WARN" "non détecté — l'installation des Operators sera bloquée"
}

if (Test-OcAccess -Verb 'create' -Resource 'clusterrolebindings.rbac.authorization.k8s.io') {
    Add-Check "create clusterrolebindings" "OK" "RBAC suffisant pour Bootstrap-IUN étape 4"
} else {
    Add-Check "create clusterrolebindings" "WARN" "refusé — l'étape RBAC du bootstrap échouera"
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
try {
    $projects = Get-OcJson -OcArgs @('get','projects')
    $n = if ($projects.items) { $projects.items.Count } else { 0 }
    Add-Check "Nb total de projects visibles" "INFO" "$n"
} catch {
    Add-Check "Nb total de projects visibles" "WARN" "lecture KO : $($_.Exception.Message)"
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

try {
    $csv = Get-OcJson -OcArgs @('get','csv','-n','openshift-operators')
    $gitopsCsvs = @($csv.items | Where-Object { $_.metadata.name -match 'gitops' })
    if ($gitopsCsvs.Count -gt 0) {
        foreach ($c in $gitopsCsvs) {
            $phase  = if ($c.status.phase) { $c.status.phase } else { 'Unknown' }
            $status = if ($phase -eq 'Succeeded') { 'OK' } else { 'WARN' }
            Add-Check "CSV $($c.metadata.name) ($phase)" $status ""
        }
    } else {
        Add-Check "CSV openshift-gitops-operator" "FAIL" "aucun CSV gitops dans openshift-operators"
    }
} catch {
    Add-Check "Inventaire CSV" "WARN" "oc get csv KO : $($_.Exception.Message)"
}

# État instance Argo CD partagée (diag uniquement)
$argoSharedNs = Invoke-OcQuiet -OcArgs @('get','namespace','openshift-gitops','--ignore-not-found','-o','name')
if ($argoSharedNs.Code -eq 0 -and $argoSharedNs.Output) {
    try {
        $pods = Get-OcJson -OcArgs @('get','pods','-n','openshift-gitops')
        $items   = @($pods.items)
        $total   = $items.Count
        $running = @($items | Where-Object { $_.status.phase -eq 'Running' }).Count
        Add-Check "Pods instance Argo CD partagée" "INFO" "$running/$total Running dans openshift-gitops"
    } catch {
        Add-Check "Pods instance Argo CD partagée" "WARN" "lecture pods KO : $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------------------
# Operators socle déjà installés (les 11 du §4)
# ---------------------------------------------------------------------------
Add-Section "Operators socle (sur les 11 cibles)"

# Patterns appliqués UNIQUEMENT sur le NAME du CSV (pas sur le namespace),
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

# Approche refactor : on parse `oc get csv -A -o json` puis filtrage PowerShell
# pur sur $csv.items (plus de jsonpath fragile sur Windows PS 5.1).
try {
    $csvAll = Get-OcJson -OcArgs @('get','csv','-A')
    $allItems = @($csvAll.items)

    foreach ($op in $socleCheck) {
        $hit = $allItems | Where-Object { $_.metadata.name -match $op.Pattern } | Select-Object -First 1

        if ($hit) {
            $ns     = $hit.metadata.namespace
            $name   = $hit.metadata.name
            $phase  = if ($hit.status.phase) { $hit.status.phase } else { 'Unknown' }
            $detail = "$name ($ns) — $phase"
            $status = if ($phase -eq 'Succeed