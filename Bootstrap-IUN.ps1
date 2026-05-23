<#
.SYNOPSIS
    Amorce une instance Argo CD dédiée IUN sur un cluster OpenShift où
    l'OpenShift GitOps Operator est déjà installé.

.DESCRIPTION
    Bootstrap-IUN.ps1 déploie le socle GitOps du programme IUN Sénégal sous forme
    d'une instance Argo CD dédiée (`iun-argocd` dans le namespace `iun-gitops`),
    distincte de l'instance Argo CD partagée du cluster.

    Hypothèses cluster (validées par diagnostic préalable) :
      - OCP 4.16.x, cluster `https://api.origins.heritage.africa:6443`
      - Utilisateur cluster-admin
      - OpenShift GitOps Operator déjà installé cluster-wide
        (CSV présent dans openshift-operators, pods openshift-gitops-* Running)
      - Certificat API expiré → --insecure-skip-tls-verify=true partout

    Le script enchaîne 7 étapes idempotentes (toutes en `oc apply`) :
        1. Vérifie la présence du CSV openshift-gitops-operator
        2. Namespace iun-gitops                    (bootstrap/00-iun-gitops-namespace.yaml)
        3. Custom Resource ArgoCD iun-argocd       (bootstrap/01-iun-argocd.yaml)
        4. ClusterRoleBinding cluster-admin        (bootstrap/02-iun-rbac.yaml)
        5. AppProject iun-platform                 (bootstrap/03-root-app-project.yaml)
        6. Root Application iun-root-<env>         (bootstrap/04-root-application.yaml)
        7. Récap : URL + commande admin password + état des Applications

    Le ClusterRoleBinding cluster-admin (étape 4) est de la DETTE TECHNIQUE assumée
    pour le PoC — à durcir en ClusterRole fine-grained avant prod.

    Un journal horodaté est écrit dans C:\IUN_APP\gitops\.logs\bootstrap-YYYYMMDD-HHMMSS.log.

.PARAMETER Server
    URL de l'API OpenShift cible.
    Défaut : https://api.origins.heritage.africa:6443

.PARAMETER RepoUrl
    URL du repo GitOps (check d'accessibilité + injection dans le manifest 04-...).
    Défaut : https://github.com/enguser-1/iun-gitops.git

.PARAMETER Environment
    Environnement cible : dev, staging ou prod. Détermine le metadata.name de la
    Root Application (iun-root-<env>) et son source.path (environments/<env>).
    Défaut : dev

.PARAMETER GitopsPath
    Chemin racine du repo GitOps local (doit contenir bootstrap/, apps/, environments/).
    Défaut : C:\IUN_APP\gitops

.PARAMETER ArgoNamespace
    Namespace de l'instance Argo CD dédiée. Défaut : iun-gitops.

.PARAMETER ArgoName
    Nom de la CR ArgoCD (et préfixe des Deployments / Routes / Secrets associés).
    Défaut : iun-argocd.

.PARAMETER SkipTlsVerify
    Si vrai, ajoute --insecure-skip-tls-verify=true à toutes les commandes oc.
    Défaut : $true (le certificat API du cluster est expiré).

.PARAMETER DryRun
    Si vrai, affiche les commandes oc qui seraient exécutées sans les lancer.

.EXAMPLE
    PS> .\Bootstrap-IUN.ps1
    Bootstrap dev complet, instance Argo CD dédiée iun-argocd / iun-gitops.

.EXAMPLE
    PS> .\Bootstrap-IUN.ps1 -DryRun
    Mode simulation : affiche chaque oc apply / oc wait sans toucher au cluster.

.EXAMPLE
    PS> .\Bootstrap-IUN.ps1 -Environment staging
    Bootstrap pour l'overlay environments/staging.

.NOTES
    Auteur  : Lead Senior Dev IUN
    Cible   : OpenShift Container Platform 4.16, PowerShell 5.1+ / 7+
    Pré-req : oc dans le PATH, `oc login` déjà effectué (cluster-admin).
#>

[CmdletBinding()]
param(
    [string] $Server        = 'https://api.origins.heritage.africa:6443',
    [string] $RepoUrl       = 'https://github.com/enguser-1/iun-gitops.git',
    [ValidateSet('dev','staging','prod')]
    [string] $Environment   = 'dev',
    [string] $GitopsPath    = 'C:\IUN_APP\gitops',
    [string] $ArgoNamespace = 'iun-gitops',
    [string] $ArgoName      = 'iun-argocd',
    [switch] $SkipTlsVerify = $true,
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
$script:HasFailed      = $false

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
$LogDir = Join-Path $GitopsPath '.logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir ("bootstrap-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))

function Write-Log {
    param(
        [string] $Message,
        [ValidateSet('INFO','WARN','ERROR','OK','STEP','DRYRUN','DEBT')]
        [string] $Level = 'INFO'
    )
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line  = "[{0}] [{1,-6}] {2}" -f $stamp, $Level, $Message
    $color = switch ($Level) {
        'OK'     { 'Green' }
        'WARN'   { 'Yellow' }
        'ERROR'  { 'Red' }
        'STEP'   { 'Cyan' }
        'DRYRUN' { 'Magenta' }
        'DEBT'   { 'DarkYellow' }
        default  { 'Gray' }
    }
    Write-Host $line -ForegroundColor $color
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Get-OcFlags {
    if ($SkipTlsVerify) { return @('--insecure-skip-tls-verify=true') }
    return @()
}

function Invoke-Oc {
    <#
        Wrapper unique autour de `oc`. Gère :
          - injection automatique des flags TLS
          - mode dry-run (affiche la commande, ne l'exécute pas)
          - capture stdout/stderr, propage le code retour
    #>
    param(
        [Parameter(Mandatory)] [string[]] $OcArgs,
        [switch] $AllowFailure
    )
    $full = @('oc') + (Get-OcFlags) + $OcArgs
    $cmd  = ($full -join ' ')

    if ($DryRun) {
        Write-Log $cmd -Level DRYRUN
        return ''
    }

    Write-Log "exec: $cmd" -Level INFO
    try {
        $output = & $full[0] $full[1..($full.Count - 1)] 2>&1
        $code   = $LASTEXITCODE
        if ($code -ne 0 -and -not $AllowFailure) {
            throw "oc a renvoyé code $code : $output"
        }
        return ($output | Out-String).TrimEnd()
    }
    catch {
        if ($AllowFailure) {
            Write-Log "oc en échec (toléré) : $($_.Exception.Message)" -Level WARN
            return $null
        }
        throw
    }
}

function Test-CommandExists {
    param([string] $Name)
    return [bool](Get-Command -Name $Name -ErrorAction SilentlyContinue)
}

function Resolve-RootApplicationManifest {
    <#
        Charge bootstrap/04-root-application.yaml et patche :
          - metadata.name -> iun-root-<env>
          - source.path   -> environments/<env>
          - source.repoURL -> $RepoUrl
        Écrit le résultat dans .logs\rendered-root-application-<env>.yaml et renvoie son chemin.
    #>
    param([string] $SourceFile, [string] $Env, [string] $Repo)

    $raw = Get-Content -Path $SourceFile -Raw

    $patched = $raw `
        -replace 'name:\s*iun-root-\w+',     "name: iun-root-$Env" `
        -replace 'path:\s*environments/\w+', "path: environments/$Env" `
        -replace 'repoURL:\s*\S+',           "repoURL: $Repo"

    $rendered = Join-Path $LogDir ("rendered-root-application-{0}.yaml" -f $Env)
    Set-Content -Path $rendered -Value $patched -Encoding UTF8 -NoNewline
    return $rendered
}

# ---------------------------------------------------------------------------
# Bannière
# ---------------------------------------------------------------------------
Write-Log "================================================================" -Level STEP
Write-Log " IUN — Bootstrap GitOps OpenShift (instance Argo CD dédiée)"      -Level STEP
Write-Log "================================================================" -Level STEP
Write-Log "Log file       : $LogFile"
Write-Log "Server cible   : $Server"
Write-Log "Repo GitOps    : $RepoUrl"
Write-Log "Environnement  : $Environment"
Write-Log "Gitops path    : $GitopsPath"
Write-Log "Argo CD NS     : $ArgoNamespace"
Write-Log "Argo CD name   : $ArgoName"
Write-Log "SkipTlsVerify  : $SkipTlsVerify"
Write-Log "DryRun         : $DryRun"
Write-Log "================================================================" -Level STEP

# ---------------------------------------------------------------------------
# Préchecks
# ---------------------------------------------------------------------------
Write-Log "Préchecks ..." -Level STEP

# 1) oc dans le PATH
if (-not (Test-CommandExists 'oc')) {
    Write-Log "oc introuvable dans le PATH. Installe l'OpenShift CLI 4.16+ et réessaye." -Level ERROR
    exit 2
}
Write-Log "oc trouvé : $(Get-Command oc | Select-Object -ExpandProperty Source)" -Level OK

# 2) oc whoami
$flags  = Get-OcFlags
$whoami = & oc whoami @flags 2>&1
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($whoami)) {
    Write-Log "Pas de session oc active. Exécute d'abord :" -Level ERROR
    Write-Log "    oc login $Server --insecure-skip-tls-verify=true" -Level ERROR
    exit 3
}
Write-Log "Utilisateur authentifié : $whoami" -Level OK

# 3) serveur réellement utilisé
$currentServer = & oc whoami @flags --show-server 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Log "Server actif   : $currentServer"
    if ($currentServer.Trim() -ne $Server.Trim()) {
        Write-Log "Le serveur actif diffère du paramètre -Server. On continue sur le contexte actif." -Level WARN
    }
}

# 4) version OCP
$verJson = & oc version @flags -o json 2>&1
if ($LASTEXITCODE -eq 0) {
    try {
        $ver = ($verJson | ConvertFrom-Json).openshiftVersion
        if ($ver) { Write-Log "OpenShift      : $ver" }
    } catch { Write-Log "Version OCP non parsable (ignorable)" -Level WARN }
} else {
    Write-Log "Impossible de récupérer la version OCP (ignorable)" -Level WARN
}

# 5) cluster-admin requis (l'instance dédiée + ClusterRoleBinding l'exigent)
$canClusterAdmin = & oc auth @flags can-i '*' '*' --all-namespaces 2>&1
if ($LASTEXITCODE -ne 0 -or $canClusterAdmin.Trim().ToLower() -ne 'yes') {
    Write-Log "cluster-admin requis pour créer l'instance ArgoCD dédiée + ClusterRoleBinding." -Level ERROR
    Write-Log "Réponse 'oc auth can-i * * --all-namespaces' = $canClusterAdmin" -Level ERROR
    exit 4
}
Write-Log "Privilèges     : cluster-admin (OK)" -Level OK

# 6) OpenShift GitOps Operator déjà installé ?
$csvCheck = & oc get @flags csv -n openshift-operators -o name 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "Impossible d'inventorier les CSVs : $csvCheck" -Level ERROR
    exit 5
}
$gitopsCsv = $csvCheck | Select-String -SimpleMatch 'gitops'
if (-not $gitopsCsv) {
    Write-Log "OpenShift GitOps Operator absent du namespace openshift-operators." -Level ERROR
    Write-Log "Ce script suppose l'Operator déjà installé cluster-wide. Installe-le d'abord ou réajuste le scope." -Level ERROR
    exit 6
}
Write-Log "OpenShift GitOps Operator présent : $($gitopsCsv -join ', ')" -Level OK

# 7) Accessibilité du repo (best-effort)
try {
    $resp = Invoke-WebRequest -Uri $RepoUrl -Method Head -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop
    Write-Log "Repo HEAD      : HTTP $($resp.StatusCode) — accessible" -Level OK
}
catch {
    $status = $null
    if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
    if ($status -in 401,403) {
        Write-Log "Repo HEAD      : HTTP $status — repo probablement privé (normal)" -Level OK
    } else {
        Write-Log "Repo HEAD inaccessible ($($_.Exception.Message))." -Level WARN
        Write-Log "Argo CD ne pourra synchroniser que si le cluster atteint cette URL." -Level WARN
    }
}

# 8) Structure GitOps locale (nouveaux fichiers bootstrap)
$expected = @(
    'bootstrap\00-iun-gitops-namespace.yaml',
    'bootstrap\01-iun-argocd.yaml',
    'bootstrap\02-iun-rbac.yaml',
    'bootstrap\03-root-app-project.yaml',
    'bootstrap\04-root-application.yaml',
    "environments\$Environment\kustomization.yaml"
)
$missing = @()
foreach ($rel in $expected) {
    $abs = Join-Path $GitopsPath $rel
    if (-not (Test-Path $abs)) { $missing += $rel }
}
if ($missing.Count -gt 0) {
    Write-Log "Fichiers manquants dans $GitopsPath :" -Level ERROR
    $missing | ForEach-Object { Write-Log "    - $_" -Level ERROR }
    Write-Log "Pull la dernière version du repo (refactor bootstrap en cours)." -Level ERROR
    exit 7
}
Write-Log "Structure GitOps locale conforme." -Level OK

# ---------------------------------------------------------------------------
# Étape 1/7 — Namespace iun-gitops
# ---------------------------------------------------------------------------
Write-Log "Étape 1/7 — Namespace $ArgoNamespace" -Level STEP
$nsFile = Join-Path $GitopsPath 'bootstrap\00-iun-gitops-namespace.yaml'
try {
    Invoke-Oc -OcArgs @('apply','-f', $nsFile) | Out-Null
    Write-Log "Namespace $ArgoNamespace appliqué." -Level OK
}
catch {
    Write-Log "Échec étape 1 : $($_.Exception.Message)" -Level ERROR
    $script:HasFailed = $true; exit 10
}

# ---------------------------------------------------------------------------
# Étape 2/7 — CR ArgoCD iun-argocd
# ---------------------------------------------------------------------------
Write-Log "Étape 2/7 — Custom Resource ArgoCD $ArgoName (ns $ArgoNamespace)" -Level STEP
$argoFile = Join-Path $GitopsPath 'bootstrap\01-iun-argocd.yaml'
try {
    Invoke-Oc -OcArgs @('apply','-f', $argoFile) | Out-Null
    Write-Log "CR ArgoCD $ArgoName appliquée." -Level OK
}
catch {
    Write-Log "Échec étape 2 : $($_.Exception.Message)" -Level ERROR
    Write-Log "L'Operator OpenShift GitOps doit avoir le CRD argoproj.io/ArgoCD : oc get crd argocds.argoproj.io" -Level ERROR
    $script:HasFailed = $true; exit 20
}

# ---------------------------------------------------------------------------
# Étape 3/7 — Attente du Deployment <name>-server
# ---------------------------------------------------------------------------
Write-Log "Étape 3/7 — Attente du Deployment $ArgoName-server (ns $ArgoNamespace)" -Level STEP
try {
    # Le Deployment peut mettre 30-90s à être créé par l'Operator après l'apply de la CR
    $tries = 0
    $deployFound = $false
    while ($tries -lt 30) {
        $d = Invoke-Oc -OcArgs @('get','deployment',"$ArgoName-server",'-n',$ArgoNamespace,'--ignore-not-found','-o','name') -AllowFailure
        if ($d) { $deployFound = $true; break }
        Start-Sleep -Seconds 5
        $tries++
    }
    if (-not $deployFound -and -not $DryRun) {
        throw "Deployment $ArgoName-server pas créé après 150s"
    }
    Invoke-Oc -OcArgs @('wait','--for=condition=Available','--timeout=600s',"deployment/$ArgoName-server",'-n',$ArgoNamespace) | Out-Null
    Write-Log "Deployment $ArgoName-server disponible." -Level OK
}
catch {
    Write-Log "$ArgoName-server n'est pas Ready : $($_.Exception.Message)" -Level ERROR
    Write-Log "Inspecte :  oc get pods -n $ArgoNamespace" -Level ERROR
    Write-Log "          oc get argocd $ArgoName -n $ArgoNamespace -o yaml" -Level ERROR
    $script:HasFailed = $true; exit 30
}

# ---------------------------------------------------------------------------
# Étape 4/7 — ClusterRoleBinding cluster-admin (DETTE TECHNIQUE)
# ---------------------------------------------------------------------------
Write-Log "Étape 4/7 — RBAC : ClusterRoleBinding cluster-admin pour le controller" -Level STEP
$rbacFile = Join-Path $GitopsPath 'bootstrap\02-iun-rbac.yaml'
try {
    Invoke-Oc -OcArgs @('apply','-f', $rbacFile) | Out-Null
    Write-Log "RBAC appliqué (ServiceAccount $ArgoName-argocd-application-controller → cluster-admin)." -Level OK
    Write-Log "DETTE TECH : binding cluster-admin pour le PoC. À durcir en ClusterRole fine-grained avant prod." -Level DEBT
}
catch {
    Write-Log "Échec étape 4 : $($_.Exception.Message)" -Level ERROR
    $script:HasFailed = $true; exit 40
}

# ---------------------------------------------------------------------------
# Étape 5/7 — AppProject iun-platform
# ---------------------------------------------------------------------------
Write-Log "Étape 5/7 — AppProject iun-platform (ns $ArgoNamespace)" -Level STEP
$projFile = Join-Path $GitopsPath 'bootstrap\03-root-app-project.yaml'
try {
    Invoke-Oc -OcArgs @('apply','-f', $projFile) | Out-Null
    Write-Log "AppProject iun-platform appliqué." -Level OK
}
catch {
    Write-Log "Échec étape 5 : $($_.Exception.Message)" -Level ERROR
    $script:HasFailed = $true; exit 50
}

# ---------------------------------------------------------------------------
# Étape 6/7 — Root Application iun-root-<env>
# ---------------------------------------------------------------------------
Write-Log "Étape 6/7 — Root Application iun-root-$Environment (path environments/$Environment)" -Level STEP
$rootSrc = Join-Path $GitopsPath 'bootstrap\04-root-application.yaml'
try {
    $rendered = Resolve-RootApplicationManifest -SourceFile $rootSrc -Env $Environment -Repo $RepoUrl
    Write-Log "Manifest rendu : $rendered"
    Invoke-Oc -OcArgs @('apply','-f', $rendered) | Out-Null
    Write-Log "Root Application appliquée." -Level OK
}
catch {
    Write-Log "Échec étape 6 : $($_.Exception.Message)" -Level ERROR
    $script:HasFailed = $true; exit 60
}

# ---------------------------------------------------------------------------
# Étape 7/7 — Récap : URL + password + Applications
# ---------------------------------------------------------------------------
Write-Log "Étape 7/7 — Accès console + état des Applications" -Level STEP

Write-Log "Pour récupérer l'URL et le mot de passe admin Argo CD, exécute :" -Level INFO
Write-Log "    .\Get-ArgoCD-Admin.ps1   # défauts adaptés à l'instance iun-argocd / iun-gitops" -Level INFO
Write-Log "Ou en commandes brutes :" -Level INFO
$flagStr = (Get-OcFlags) -join ' '
Write-Log ("    oc $flagStr get route $ArgoName-server -n $ArgoNamespace -o jsonpath='{`"https://`"}{.spec.host}{`"\n`"}'") -Level INFO
Write-Log ("    oc $flagStr get secret $ArgoName-cluster -n $ArgoNamespace -o jsonpath='{.data.admin\.password}' | %% { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(`$_)) }") -Level INFO

if ($DryRun) {
    Write-Log "Dry-run : récap des Applications ignoré." -Level DRYRUN
}
else {
    Start-Sleep -Seconds 3
    $apps = Invoke-Oc -OcArgs @(
        'get','applications','-n', $ArgoNamespace,
        '-o','custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'
    ) -AllowFailure
    if ($apps) {
        Write-Log "Applications visibles dans $ArgoNamespace :"
        $apps -split "`n" | ForEach-Object { Write-Log "    $_" }
    } else {
        Write-Log "Aucune Application visible pour le moment (peut prendre 30-60s)." -Level WARN
    }
}

# ---------------------------------------------------------------------------
# Sortie
# ---------------------------------------------------------------------------
if ($script:HasFailed) {
    Write-Log "Bootstrap terminé AVEC erreurs — voir log $LogFile" -Level ERROR
    exit 1
}
Write-Log "Bootstrap terminé avec succès. Log : $LogFile" -Level OK
exit 0
