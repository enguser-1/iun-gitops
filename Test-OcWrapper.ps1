<#
.SYNOPSIS
    Harness de tests pour le module IunOc.psm1.

.DESCRIPTION
    Valide en bout-en-bout que les wrappers contournent bien les quirks de
    quoting Windows PowerShell 5.1 qui cassaient l'ancien pattern
    `& oc <subcmd> <args> @flags 2>&1`. Couvre :

      - ConvertTo-Win32Arg   : escape MSVC (cas '*', '.', '-', quotes, espaces)
      - Invoke-Oc            : oc whoami → Stdout non vide, ExitCode=0
      - Get-OcJson           : oc get projects → .items énumérable
      - Test-OcAccess        : wildcards préservés ('*','*'), names avec '.',
                               valeur retournée booléenne

    Le harness imprime un récap PASS/FAIL et retourne :
      0 si tous les tests sont OK
      1 dès qu'un test FAIL

.PARAMETER SkipTlsVerify
    Ajoute --insecure-skip-tls-verify=true (cert cluster IUN expiré). Défaut : $true.

.PARAMETER SkipClusterTests
    N'exécute que les tests unitaires (escape), pas les tests cluster live.
    Utile pour valider le module sans avoir oc login fait.

.EXAMPLE
    PS> .\Test-OcWrapper.ps1
    Suite complète (escape + tests live sur le cluster connecté).

.EXAMPLE
    PS> .\Test-OcWrapper.ps1 -SkipClusterTests
    Seulement les tests d'escape (offline).

.NOTES
    À jouer après tout refactor de IunOc.psm1 et avant chaque rejouage de
    Bootstrap-IUN.ps1 sur le cluster cible.
#>

[CmdletBinding()]
param(
    [switch] $SkipTlsVerify    = $true,
    [switch] $SkipClusterTests
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Import du module
# ---------------------------------------------------------------------------
$ModulePath = Join-Path $PSScriptRoot 'IunOc.psm1'
if (-not (Test-Path $ModulePath)) {
    Write-Error "IunOc.psm1 introuvable ($ModulePath)."
    exit 2
}
Import-Module $ModulePath -Force -DisableNameChecking

if ($SkipTlsVerify) {
    Set-OcGlobalFlags -Flags @('--insecure-skip-tls-verify=true')
} else {
    Set-OcGlobalFlags -Flags @()
}

# ---------------------------------------------------------------------------
# Mini-framework
# ---------------------------------------------------------------------------
$Script:results = New-Object 'System.Collections.Generic.List[pscustomobject]'

function Assert-Test {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [scriptblock] $Script
    )
    Write-Host ""
    Write-Host "RUN  : $Name" -ForegroundColor Cyan
    try {
        & $Script
        Write-Host "PASS : $Name" -ForegroundColor Green
        $Script:results.Add([pscustomobject]@{ Name=$Name; Status='PASS'; Error='' })
    }
    catch {
        Write-Host "FAIL : $Name — $($_.Exception.Message)" -ForegroundColor Red
        $Script:results.Add([pscustomobject]@{ Name=$Name; Status='FAIL'; Error=$_.Exception.Message })
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string] $Label = 'value')
    if ($Expected -ne $Actual) {
        throw "Assert-Equal($Label) : attendu <$Expected>, obtenu <$Actual>"
    }
}

function Assert-True {
    param([bool] $Cond, [string] $Label = 'condition')
    if (-not $Cond) { throw "Assert-True($Label) : faux" }
}

# ---------------------------------------------------------------------------
# 1. Tests unitaires : ConvertTo-Win32Arg (escape MSVC)
# ---------------------------------------------------------------------------
Assert-Test "ConvertTo-Win32Arg : argument simple non échappé" {
    Assert-Equal 'oc'                     (ConvertTo-Win32Arg 'oc')                     'simple'
    Assert-Equal 'create'                 (ConvertTo-Win32Arg 'create')                 'simple'
    Assert-Equal 'clusterrolebindings.rbac.authorization.k8s.io' `
        (ConvertTo-Win32Arg 'clusterrolebindings.rbac.authorization.k8s.io') 'dot-name'
}

Assert-Test "ConvertTo-Win32Arg : wildcard '*' non absorbé" {
    # Le bug historique : '*' était absorbé par .NET ProcessStartInfo. Ici le
    # wildcard ne contient ni espace ni quote ; il passe tel quel sur la ligne
    # de commande Windows, ce qui est exactement ce qu'on veut.
    Assert-Equal '*' (ConvertTo-Win32Arg '*') 'wildcard'
}

Assert-Test "ConvertTo-Win32Arg : argument avec espaces" {
    Assert-Equal '"hello world"' (ConvertTo-Win32Arg 'hello world') 'spaces'
}

Assert-Test "ConvertTo-Win32Arg : argument vide" {
    Assert-Equal '""' (ConvertTo-Win32Arg '') 'empty'
}

Assert-Test "ConvertTo-Win32Arg : guillemets internes" {
    # foo"bar  →  "foo\"bar"
    Assert-Equal '"foo\"bar"' (ConvertTo-Win32Arg 'foo"bar') 'inner-quote'
}

Assert-Test "ConvertTo-Win32Arg : jsonpath avec accolades et tab" {
    # jsonpath={range .items[*]}{.metadata.name}{"\n"}{end}
    # Il contient des espaces, donc doit être quoté ; les "\n" internes sont
    # protégés par doublement des backslashes précédant le ".
    $jp = '{range .items[*]}{.metadata.name}{"\n"}{end}'
    $escaped = ConvertTo-Win32Arg $jp
    Assert-True ($escaped.StartsWith('"') -and $escaped.EndsWith('"')) 'wrapped-in-quotes'
    # Les guillemets internes doivent être préfixés par un \.
    Assert-True ($escaped -match '\\"') 'inner-quotes-escaped'
}

# ---------------------------------------------------------------------------
# 2. Tests live (cluster connecté requis)
# ---------------------------------------------------------------------------
if ($SkipClusterTests) {
    Write-Host ""
    Write-Host "Tests cluster ignorés (-SkipClusterTests)." -ForegroundColor Yellow
} else {
    # Précheck oc présent + session active.
    if (-not (Get-Command oc -ErrorAction SilentlyContinue)) {
        Write-Error "oc introuvable dans le PATH — tests cluster impossibles."
        exit 2
    }

    Assert-Test "Invoke-Oc : oc whoami renvoie un user dans .Stdout" {
        $r = Invoke-Oc -OcArgs @('whoami') -TimeoutSeconds 30
        Assert-Equal 0 $r.ExitCode 'exitcode'
        Assert-True (-not [string]::IsNullOrWhiteSpace($r.Stdout)) 'stdout-non-empty'
    }

    Assert-Test "Get-OcJson : oc get projects retourne .items" {
        $j = Get-OcJson -OcArgs @('get','projects')
        Assert-True ($null -ne $j.items) 'items-present'
        # .items doit être énumérable (peut être vide si user sans projects).
        Assert-True ($j.items -is [System.Collections.IEnumerable]) 'items-iterable'
    }

    Assert-Test "Test-OcAccess : '*' '*' --all-namespaces ne plante pas" {
        # Le bug historique faisait "you must specify two arguments". Ici le
        # call ne doit lever AUCUNE exception, et renvoyer un booléen.
        $r = Test-OcAccess -Verb '*' -Resource '*' -AllNamespaces
        Assert-True (($r -eq $true) -or ($r -eq $false)) 'returns-bool'
    }

    Assert-Test "Test-OcAccess : create namespaces (cluster-admin)" {
        $r = Test-OcAccess -Verb 'create' -Resource 'namespaces'
        Assert-True ($r -eq $true) 'cluster-admin-can-create-ns'
    }

    Assert-Test "Test-OcAccess : nom avec points (clusterrolebindings.rbac...)" {
        # Le bug historique faisait que les '.' tokenisaient mal. Ici ça doit
        # passer proprement et renvoyer $true (cluster-admin).
        $r = Test-OcAccess -Verb 'create' -Resource 'clusterrolebindings.rbac.authorization.k8s.io'
        Assert-True ($r -eq $true) 'dotted-resource-name-handled'
    }

    Assert-Test "Get-OcJson : oc get csv -A (équivalent jsonpath qui cassait)" {
        # Anciennement : oc get csv -A -o jsonpath='{...{\t}...}' explosait
        # ("unrecognized character U+005C"). Ici on passe par -o json et
        # un filtrage PowerShell pur sur .items.
        $j = Get-OcJson -OcArgs @('get','csv','-A')
        Assert-True ($null -ne $j.items) 'items-present'
    }
}

# ---------------------------------------------------------------------------
# Synthèse
# ---------------------------------------------------------------------------
$pass = ($Script:results | Where-Object { $_.Status -eq 'PASS' }).Count
$fail = ($Script:results | Where-Object { $_.Status -eq 'FAIL' }).Count

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host (" PASS : {0}" -f $pass) -ForegroundColor Green
Write-Host (" FAIL : {0}" -f $fail) -ForegroundColor (if ($fail) { 'Red' } else { 'Gray' })
Write-Host "================================================" -ForegroundColor Cyan

if ($fail -gt 0) {
    Write-Host ""
    Write-Host "Échecs :" -ForegroundColor Red
    $Script:results | Where-Object { $_.Status -eq 'FAIL' } | ForEach-Object {
        Write-Host ("  - {0} : {1}" -f $_.Name, $_.Error) -ForegroundColor Red
    }
    exit 1
}

exit 0
