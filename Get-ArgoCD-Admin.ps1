<#
.SYNOPSIS
    Récupère l'URL et le mot de passe admin initial d'une instance Argo CD.

.DESCRIPTION
    Get-ArgoCD-Admin.ps1 interroge le cluster pour :
        - la Route <ArgoName>-server (namespace <Namespace>)  → URL HTTPS
        - le Secret <ArgoName>-cluster.data.admin\.password  → mot de passe initial

    Affiche les deux à l'écran. Optionnellement, copie le mot de passe dans le presse-papier
    Windows (-Clipboard) et/ou ouvre la console dans le navigateur (-Open).

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

function Get-OcFlags {
    if ($SkipTlsVerify) { return @('--insecure-skip-tls-verify=true') }
    return @()
}

# Préchecks
if (-not (Get-Command oc -ErrorAction SilentlyContinue)) {
    Write-Error "oc introuvable dans le PATH."
    exit 2
}
$flags = Get-OcFlags
$null  = & oc whoami @flags 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Aucune session oc. Lance d'abord :  oc login <server> --insecure-skip-tls-verify=true"
    exit 3
}

# URL de la Route
$RouteName = "$ArgoName-server"
$SecretName = "$ArgoName-cluster"

Write-Host "Recherche Route $RouteName dans $Namespace ..." -ForegroundColor Cyan
$RouteHost = & oc get @flags route $RouteName -n $Namespace -o jsonpath='{.spec.host}' 2>&1
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($RouteHost)) {
    Write-Error "Route $RouteName introuvable dans $Namespace (Operator pas encore prêt, ou mauvais ArgoName)."
    exit 10
}
$Url = "https://$RouteHost"
Write-Host ""
Write-Host "URL Argo CD     : " -NoNewline
Write-Host $Url -ForegroundColor Green

# Mot de passe admin initial
Write-Host "Recherche Secret $SecretName.data.admin.password ..." -ForegroundColor Cyan
$b64 = & oc get @flags secret $SecretName -n $Namespace -o jsonpath='{.data.admin\.password}' 2>&1

if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($b64)) {
    Write-Warning "Secret $SecretName ou clé admin.password introuvable."
    Write-Warning "Cas possible : l'admin local a été remplacé par un IdP (Keycloak/RHBK)."
    Write-Warning "Connecte-toi alors via 'LOG IN VIA <IdP>' sur la console."
    if ($Open) { Start-Process $Url }
    exit 0
}

try {
    $Password = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($b64.Trim()))
}
catch {
    Write-Error "Décodage Base64 du mot de passe en échec : $($_.Exception.Message)"
    exit 11
}

Write-Host "User            : " -NoNewline; Write-Host "admin" -ForegroundColor Green
Write-Host "Password        : " -NoNewline; Write-Host $Password -ForegroundColor Green
Write-Host ""

if ($Clipboard) {
    try {
        $Password | Set-Clipboard
        Write-Host "Mot de passe copié dans le presse-papier." -ForegroundColor Yellow
    } catch {
        Write-Warning "Set-Clipboard indisponible : $($_.Exception.Message)"
    }
}

if ($Open) {
    Write-Host "Ouverture de $Url dans le navigateur par défaut ..." -ForegroundColor Yellow
    Start-Process $Url
}
