<#
.SYNOPSIS
    Récupère l'URL et le mot de passe admin initial d'une instance Argo CD.

.DESCRIPTION
    Get-ArgoCD-Admin.ps1 interroge le cluster pour :
        - la Route <ArgoName>-server (namespace <Namespace>)  → URL HTTPS
        - le Secret <ArgoName>-cluster.data.admin\.password  → mot de passe initial

    Affiche les deux à l'écran. Optionnellement, copie le mot de passe dans le presse-papier
    Windows (-Clipboard) et/ou ouvre la console dans le navigateur (-Open).

    Tous les appels oc passent par le module IunOc.psm1 (wrappers robustes
    Windows PowerShell 5.1).

    Défauts adaptés à l'instance dédiée IUN (iun-argocd / iun-gitops). Pour interroger
    l'instance partagée openshift-gitops, passer -Namespace openshift-gitops -ArgoName openshift-gitops.

.PARAMETER Namespace
    Namespace de l'instance ArgoCD. Défaut : iun-gitops.

.PARAMETER ArgoName
    Nom de la CR ArgoCD (préfixe des Route/Secret). Défaut : iun-argocd.

.PARAMETER SkipTlsVerify
    Ajoute --insecure-skip-tls-verify=true à oc. Défaut : $true (cert cluster expiré).

.PARAMETER Clipboard
    Copie le mot de passe admin dans le presse-papier Windows.

.PARAMETER Open
    Ouvre l'URL Argo CD dans le navigateur par défaut.

.EXAMPLE
    PS> .\Get-ArgoCD-Admin.ps1

.EXAMPLE
    PS> .\Get-ArgoCD-Admin.ps1 -Clipboard -Open

.EXAMPLE
    PS> .\Get-ArgoCD-Admin.ps1 -Namespace openshift-gitops -ArgoName openshift-gitops
    Cible l'instance Argo CD partagée du cluster (au lieu de l'instance dédiée IUN).

.NOTES
    Si le Secret <ArgoName>-cluster n'a pas de clé admin.password, c'est typiquement
    parce que l'admin local a été remplacé par un IdP (Keycloak / RHBK) : utiliser
    alors le bouton "LOG IN VIA <IdP>" sur la console.
#>

[CmdletBinding()]
param(
    [string] $Namespace     = 'iun-gitops',
    [string] $ArgoName      = 'iun-argocd',
    [switch] $SkipTlsVerify = $true,
    [switch] $Clipboard,
    [switch] $Open
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
# Préchecks
# ---------------------------------------------------------------------------
if (-not (Get-Command oc -ErrorAction SilentlyContinue)) {
    Write-Error "oc introuvable dans le PATH."
    exit 2
}

try {
    $whoami = Invoke-Oc -OcArgs @('whoami') -TimeoutSeconds 30
    if ([string]::IsNullOrWhiteSpace($whoami.Stdout)) { throw "stdout vide" }
} catch {
    Write-Error "Aucune session oc ($($_.Exception.Message)). Lance d'abord :  oc login <server> --insecure-skip-tls-verify=true"
    exit 3
}

# ---------------------------------------------------------------------------
# URL de la Route
# ---------------------------------------------------------------------------
$RouteName  = "$ArgoName-server"
$SecretName = "$ArgoName-cluster"

Write-Host "Recherche Route $RouteName dans $Namespace ..." -ForegroundColor Cyan
try {
    $route = Get-OcJson -OcArgs @('get','route',$RouteName,'-n',$Namespace)
} catch {
    Write-Error "Route $RouteName introuvable dans $Namespace (Operator pas encore prêt, ou mauvais ArgoName). Détail : $($_.Exception.Message)"
    exit 10
}

$RouteHost = $route.spec.host
if ([string]::IsNullOrWhiteSpace($RouteHost)) {
    Write-Error "Route $RouteName trouvée mais .spec.host est vide."
    exit 10
}
$Url = "https://$RouteHost"
Write-Host ""
Write-Host "URL Argo CD     : " -NoNewline
Write-Host $Url -ForegroundColor Green

# ---------------------------------------------------------------------------
# Mot de passe admin initial
# ---------------------------------------------------------------------------
Write-Host "Recherche Secret $SecretName.data.admin.password ..." -ForegroundColor Cyan
$b64 = $null
