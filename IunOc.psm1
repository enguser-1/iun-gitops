<#
.SYNOPSIS
    Wrappers oc.exe robustes pour Windows PowerShell 5.1.

.DESCRIPTION
    Le call operator `&` de PowerShell 5.1 délègue à .NET ProcessStartInfo, qui
    construit la ligne de commande pour un executable Windows natif via une
    logique de quoting fragile. Conséquences observées sur le programme IUN :

      - oc auth can-i '*' '*' --all-namespaces  →  les '*' sont absorbés,
        oc renvoie "you must specify two arguments: verb resource".

      - oc auth can-i create clusterrolebindings.rbac.authorization.k8s.io  →
        les '.' du nom de resource perturbent la tokenization, même erreur.

      - oc get csv -n openshift-operators -o name  →  le `-` initial de `-o`
        est interprété comme un nom de resource ("- not found").

      - oc get csv -A -o jsonpath='{...{\t}...}'  →  les guillemets internes
        du jsonpath sont mangés, parsing en échec.

    Cause racine : .NET joint un `string[]` en une ligne de commande Windows en
    appliquant un quoting basique inadapté aux executables qui suivent les
    règles de parsing C/C++ (MSVC, qui est ce que `oc` utilise via Cobra +
    spf13/pflag sur Windows).

    Ce module appelle oc.exe via `System.Diagnostics.Process` en construisant
    `Arguments` à la main, avec un escape conforme aux règles MSVC
    (https://docs.microsoft.com/en-us/cpp/cpp/main-function-command-line-args).

.NOTES
    Auteur : Lead Senior Dev IUN
    Cible  : Windows PowerShell 5.1+ ; compatible PowerShell 7+.
    Encodage attendu : UTF-8 avec BOM (PS 5.1 ne parse pas les .ps1/.psm1
    UTF-8 sans BOM correctement quand des accents sont présents).
#>

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

# Flags injectés automatiquement à chaque appel oc. Modifiable via
# Set-OcGlobalFlags depuis le script appelant.
$Script:OcGlobalFlags = @('--insecure-skip-tls-verify=true')

function Set-OcGlobalFlags {
    <#
    .SYNOPSIS
        Remplace la liste des flags globaux oc (TLS, --request-timeout, ...).
    #>
    param([string[]] $Flags = @())
    $Script:OcGlobalFlags = @($Flags)
}

function Get-OcGlobalFlags {
    return ,@($Script:OcGlobalFlags)
}

# ---------------------------------------------------------------------------
# Escape Windows (règles MSVC, cf. CommandLineToArgvW)
# ---------------------------------------------------------------------------
function ConvertTo-Win32Arg {
    <#
    .SYNOPSIS
        Échappe un argument pour la ligne de commande Windows selon les règles
        MSVC parsing (CommandLineToArgvW).
    .DESCRIPTION
        Règles :
          - Si l'argument est vide → "".
          - Sinon, si l'argument ne contient ni espace ni guillemet ni tabulation,
            on le passe tel quel.
          - Sinon on l'entoure de guillemets ; à l'intérieur :
              * 2n backslashes  +  "  → 2n*2 backslashes + \"
              * 2n+1 backslashes + " → (2n+1)*2 backslashes + \"
              * Backslashes en fin (avant le " fermant) → doublés.
              * Les autres caractères passent tels quels.
    #>
    param([string] $Arg)

    if ($null -eq $Arg) { return '""' }
    if ($Arg -eq '')    { return '""' }
    if ($Arg -notmatch '[\s"]') { return $Arg }

    $sb = New-Object System.Text.StringBuilder
    [void] $sb.Append('"')
    $backslashes = 0
    foreach ($c in $Arg.ToCharArray()) {
        if ($c -eq '\') {
            $backslashes++
        }
        elseif ($c -eq '"') {
            [void] $sb.Append([string]::new('\', 2 * $backslashes + 1))
            [void] $sb.Append('"')
            $backslashes = 0
        }
        else {
            if ($backslashes -gt 0) {
                [void] $sb.Append([string]::new('\', $backslashes))
                $backslashes = 0
            }
            [void] $sb.Append($c)
        }
    }
    if ($backslashes -gt 0) {
        [void] $sb.Append([string]::new('\', 2 * $backslashes))
    }
    [void] $sb.Append('"')
    return $sb.ToString()
}

# ---------------------------------------------------------------------------
# Invoke-Oc
# ---------------------------------------------------------------------------
function Invoke-Oc {
    <#
    .SYNOPSIS
        Appelle oc.exe en contournant les quirks de quoting de Windows PS 5.1.

    .DESCRIPTION
        Utilise System.Diagnostics.Process avec un escape MSVC manuel. Les
        flags globaux ($Script:OcGlobalFlags) sont injectés en queue de la
        ligne de commande (oc 4.16 refuse certains flags avant le sous-commande).

    .PARAMETER OcArgs
        Tableau d'arguments. UN argument par élément du tableau, pas de
        concaténation ambiguë. Exemples :
          Invoke-Oc -OcArgs @('auth','can-i','*','*','--all-namespaces')
          Invoke-Oc -OcArgs @('get','route','iun-argocd-server','-n','iun-gitops','-o','jsonpath={.spec.host}')

    .PARAMETER TimeoutSeconds
        Délai max d'attente. Par défaut 120s. Si dépassé, le process est tué et
        une exception est levée.

    .PARAMETER AllowFailure
        Si présent, ne lève pas d'exception en cas d'ExitCode != 0. L'appelant
        doit alors inspecter .ExitCode.

    .OUTPUTS
        [PSCustomObject] @{ Stdout = '...'; Stderr = '...'; ExitCode = 0 }
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string[]] $OcArgs,
        [int]    $TimeoutSeconds = 120,
        [switch] $AllowFailure
    )

    # Construction args : OcArgs + flags globaux (en queue).
    $allArgs = New-Object 'System.Collections.Generic.List[string]'
    foreach ($a in $OcArgs)              { [void] $allArgs.Add([string]$a) }
    foreach ($f in $Script:OcGlobalFlags) { [void] $allArgs.Add([string]$f) }

    $escaped = ($allArgs | ForEach-Object { ConvertTo-Win32Arg $_ }) -join ' '

    Write-Verbose ("oc {0}" -f $escaped)

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = 'oc'
    $psi.Arguments              = $escaped
    $psi.UseShellExecute        = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.CreateNoWindow         = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding  = [System.Text.Encoding]::UTF8

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi

    try {
        [void] $proc.Start()
    } catch {
        throw "Impossible de lancer oc.exe : $($_.Exception.Message). Vérifie que oc est dans le PATH."
    }

    # Lecture async pour éviter les deadlocks (stdout et stderr en parallèle).
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()

    $exited = $proc.WaitForExit([int]([Math]::Min([int]::MaxValue, [long]$TimeoutSeconds * 1000)))
    if (-not $exited) {
        try { $proc.Kill() } catch { }
        throw "oc.exe : timeout après $TimeoutSeconds s (args : $($OcArgs -join ' '))"
    }

    # Sécurise la collecte stdout/stderr (le process est bien terminé ici).
    $stdoutTask.Wait()
    $stderrTask.Wait()

    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    $code   = $proc.ExitCode

    $result = [PSCustomObject]@{
        Stdout   = if ($null -ne $stdout) { $stdout.TrimEnd("`r","`n") } else { '' }
        Stderr   = if ($null -ne $stderr) { $stderr.TrimEnd("`r","`n") } else { '' }
        ExitCode = $code
    }

    if ($code -ne 0 -and -not $AllowFailure) {
        $msg = if ($result.Stderr) { $result.Stderr } else { $result.Stdout }
        throw "oc $($OcArgs -join ' ') a renvoyé code $code : $msg"
    }

    return $result
}

# ---------------------------------------------------------------------------
# Get-OcJson
# ---------------------------------------------------------------------------
function Get-OcJson {
    <#
    .SYNOPSIS
        Exécute oc avec '-o json' et renvoie l'objet désérialisé.

    .DESCRIPTION
        Wrapper qui ajoute '-o json' à la liste des OcArgs s'il n'est pas déjà
        présent, puis parse stdout via ConvertFrom-Json. Lève une exception si
        oc échoue ou si le JSON est invalide.

    .PARAMETER OcArgs
        Arguments oc (sans -o json, ajouté automatiquement).

    .PARAMETER AllowEmpty
        Si présent, renvoie $null quand oc a réussi mais stdout est vide
        (cas d'un --ignore-not-found qui ne trouve rien). Sans ce flag,
        un JSON vide est une erreur.

    .EXAMPLE
        $csvs = Get-OcJson -OcArgs @('get','csv','-n','openshift-operators')
        $csvs.items | Where-Object { $_.metadata.name -like 'openshift-gitops-operator*' }
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string[]] $OcArgs,
        [int]    $TimeoutSeconds = 120,
        [switch] $AllowEmpty
    )

    # Insère '-o json' s'il n'est pas déjà demandé (recherche "-o" suivi de "json").
    $hasJson = $false
    for ($i = 0; $i -lt $OcArgs.Count - 1; $i++) {
        if ($OcArgs[$i] -eq '-o' -and $OcArgs[$i + 1] -eq 'json') { $hasJson = $true; break }
    }
    $finalArgs = if ($hasJson) { $OcArgs } else { @($OcArgs) + @('-o','json') }

    $r = Invoke-Oc -OcArgs $finalArgs -TimeoutSeconds $TimeoutSeconds

    if ([string]::IsNullOrWhiteSpace($r.Stdout)) {
        if ($AllowEmpty) { return $null }
        throw "Get-OcJson : oc $($OcArgs -join ' ') a réussi mais stdout est vide."
    }

    try {
        return ($r.Stdout | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        throw "Get-OcJson : parsing JSON KO ($($_.Exception.Message)). Stdout (200 1ers car.) : $($r.Stdout.Substring(0, [Math]::Min(200, $r.Stdout.Length)))"
    }
}

# ---------------------------------------------------------------------------
# Test-OcAccess
# ---------------------------------------------------------------------------
function Test-OcAccess {
    <#
    .SYNOPSIS
        Wrapper booléen autour de `oc auth can-i`.

    .DESCRIPTION
        Renvoie $true si l'utilisateur courant peut faire le Verb sur la
        Resource (et éventuellement le Namespace). Préserve correctement
        les wildcards '*' et les noms de resources contenant des '.'.

    .PARAMETER Verb
        Verbe RBAC (get, list, create, update, delete, '*', ...).

    .PARAMETER Resource
        Resource (namespaces, clusterrolebindings, '*', ...).
        Peut inclure le suffixe d'API group (ex : 'clusterrolebindings.rbac.authorization.k8s.io').

    .PARAMETER Namespace
        Namespace cible. Si '', test cluster-scoped via --all-namespaces
        quand Verb et Resource sont '*' (cas cluster-admin).

    .PARAMETER AllNamespaces
        Force le test avec --all-namespaces (utile pour vérifier cluster-admin).

    .OUTPUTS
        [bool]

    .EXAMPLE
        Test-OcAccess -Verb '*' -Resource '*' -AllNamespaces

    .EXAMPLE
        Test-OcAccess -Verb create -Resource clusterrolebindings
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Verb,
        [Parameter(Mandatory)] [string] $Resource,
        [string] $Namespace = '',
        [switch] $AllNamespaces
    )

    # Attention : $args est une variable automatique en PowerShell — on utilise $authArgs.
    $authArgs = @('auth','can-i', $Verb, $Resource)
    if ($AllNamespaces) {
        $authArgs += '--all-namespaces'
    } elseif ($Namespace) {
        $authArgs += @('-n', $Namespace)
    }

    # can-i sort 1 quand la réponse est "no" → AllowFailure.
    $r = Invoke-Oc -OcArgs $authArgs -AllowFailure -TimeoutSeconds 30

    # Réponse attendue : 'yes' ou 'no' (parfois précédée d'un warning sur stderr).
    $ans = $r.Stdout.Trim().ToLower()
    return ($r.ExitCode -eq 0 -and $ans -eq 'yes')
}

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
Export-ModuleMember -Function Invoke-Oc, Get-OcJson, Test-OcAccess,
                              Set-OcGlobalFlags, Get-OcGlobalFlags,
                              ConvertTo-Win32Arg
