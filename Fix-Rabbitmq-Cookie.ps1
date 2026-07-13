<#
.SYNOPSIS
    Fix end-to-end du CrashLoopBackOff openimis-rabbitmq-0 sur cookie Erlang.

.DESCRIPTION
    Symptome :
        [error] Cookie file /var/lib/rabbitmq/.erlang.cookie must be accessible
        by owner only
        Kernel pid terminated (application_controller)
        ({application_start_failure,rabbitmq_prelaunch, ...})

    Cause :
        L'image rabbitmq:3.13-management ne fixe pas explicitement le mode du
        cookie auto-genere. Sur OCP + PVC Ceph RBD, le cookie heritait d'un
        mode 0644 (group+other readable) -> auth.erl le refuse car
        (mode band 8#077) =/= 0. Un wipe simple du PVC ne suffit pas, le main
        container regenere a chaque restart avec le meme umask.

    Fix : initContainer 'cookie-fix' ajoute dans 12-rabbitmq.yaml. Tourne en
    UID 0 (SCC anyuid deja accordee via openshift.io/required-scc), supprime
    le cookie s'il a un mode invalide, regenere via /dev/urandom (umask 077),
    force chown 999:999 + chmod 600.

    Ce script :
      1. Pre-flight (oc dans PATH, oc whoami, git, module IunOc, short-name check).
      2. Snapshot etat actuel pod.
      3. git add/commit/push de 12-rabbitmq.yaml.
      4. Discovery du nom reel de l'Application Argo CD + refresh hard + trigger
         operation.sync explicite + boucle status.sync.status (fallback oc apply).
      5. Attente sync (presence de initContainer cookie-fix dans le STS).
      6. Force-delete pod (ou wipe complet du PVC si -WipePvc).
      7. Attente terminaison initContainer cookie-fix.
      8. Affichage logs cookie-fix.
      9. Verif frontend reschedule (apres remove nodeSelector cote Lead).
     10. Tail logs rabbitmq en streaming.

.PARAMETER GitOpsRoot
    Racine repo gitops local. Defaut : C:\IUN_APP\gitops

.PARAMETER Namespace
    Namespace OpenIMIS. Defaut : openimis-dev

.PARAMETER ArgoApp
    Nom souhaite de l'Application Argo CD. Si non trouve, le script essaie
    automatiquement openimis-core, dpg-openimis, openimis, openimis-dev.

.PARAMETER WipePvc
    Si present, scale STS=0, delete PVC, scale STS=1. Defaut OFF : on tue
    juste le pod, le initContainer recupere/regenere le cookie sur place.
    Wipe utile UNIQUEMENT si les donnees broker sont aussi corrompues.

.PARAMETER SkipGitPush
    Si present, saute git add/commit/push (manifest deja merge).

.PARAMETER SkipArgoRefresh
    Si present, saute le refresh + trigger sync (utile en debug local).

.PARAMETER ForceLocalApply
    Si present, saute Argo et applique directement le YAML via oc apply -f.
    Drift Argo a corriger apres. Utile en escalation rapide.

.EXAMPLE
    .\Fix-Rabbitmq-Cookie.ps1
    # Cas nominal : push + refresh + trigger sync + delete pod + tail logs

.EXAMPLE
    .\Fix-Rabbitmq-Cookie.ps1 -SkipGitPush
    # Commit deja en main (relance etapes 3-7 uniquement)

.EXAMPLE
    .\Fix-Rabbitmq-Cookie.ps1 -SkipGitPush -ForceLocalApply
    # Bypass Argo, applique le manifest local direct

.EXAMPLE
    .\Fix-Rabbitmq-Cookie.ps1 -WipePvc
    # Reset complet broker (queues + cookie)

.EXAMPLE
    .\Fix-Rabbitmq-Cookie.ps1 -SkipGitPush -ArgoApp dpg-openimis
    # Forcer un nom d'Application Argo specifique

.NOTES
    Encodage requis : UTF-8 avec BOM (PS 5.1 + accents).
    Cluster cible  : https://api.origins.heritage.africa:6443 (TLS skip force
                     via OcGlobalFlags du module IunOc).
#>
[CmdletBinding()]
param(
    [string] $GitOpsRoot   = 'C:\IUN_APP\gitops',
    [string] $Namespace    = 'openimis-dev',
    [string] $StsName      = 'openimis-rabbitmq',
    [string] $PodName      = 'openimis-rabbitmq-0',
    [string] $PvcName      = 'data-openimis-rabbitmq-0',
    [string] $ArgoApp      = 'openimis-core',
    [string] $ArgoNs       = 'iun-gitops',
    [string] $GitRemote    = 'origin',
    [string] $GitBranch    = 'main',
    [string] $CommitMsg    = 'fix(rabbitmq): initContainer cookie-fix (chmod 600 .erlang.cookie)',
    [string] $RabbitYamlRel = 'apps/dpg/openimis/manifests/12-rabbitmq.yaml',
    [switch] $WipePvc,
    [switch] $SkipGitPush,
    [switch] $SkipArgoRefresh,
    [switch] $ForceLocalApply,
    [int]    $LogTailLines = 80,
    [int]    $SyncTimeoutSec = 150
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Helpers d'affichage
# ---------------------------------------------------------------------------
function Step  { param([string]$m) Write-Host ("`n=== {0} ===" -f $m) -ForegroundColor Cyan }
function OkMsg { param([string]$m) Write-Host ("  OK   : {0}" -f $m) -ForegroundColor Green }
function NoMsg { param([string]$m) Write-Host ("  KO   : {0}" -f $m) -ForegroundColor Red }
function WMsg  { param([string]$m) Write-Host ("  WARN : {0}" -f $m) -ForegroundColor Yellow }
function IMsg  { param([string]$m) Write-Host ("  INFO : {0}" -f $m) -ForegroundColor Gray }

# Resource FQ Argo (evite collision avec applications.app.k8s.io)
$Script:ArgoAppFQ = 'applications.argoproj.io'

# ---------------------------------------------------------------------------
# 0. Pre-flight
# ---------------------------------------------------------------------------
Step '0. Pre-flight (oc, git, module IunOc, collisions short-name)'

if (-not (Get-Command oc -ErrorAction SilentlyContinue)) {
    throw "oc.exe introuvable dans le PATH. Installe le CLI OpenShift et relance."
}
OkMsg 'oc.exe present dans le PATH'

$modulePath = Join-Path $GitOpsRoot 'IunOc.psm1'
if (-not (Test-Path $modulePath)) {
    throw "Module IunOc.psm1 introuvable a $modulePath. Verifie -GitOpsRoot."
}
Import-Module $modulePath -Force
OkMsg "module IunOc importe ($modulePath)"

$flags = Get-OcGlobalFlags
if ($flags -notcontains '--insecure-skip-tls-verify=true') {
    WMsg 'Flag --insecure-skip-tls-verify=true absent — je le rajoute'
    Set-OcGlobalFlags -Flags (@($flags) + '--insecure-skip-tls-verify=true')
}

$who = Invoke-Oc -OcArgs @('whoami') -AllowFailure
if ($who.ExitCode -ne 0) {
    throw "oc whoami a echoue : $($who.Stderr). Lance d'abord 'oc login https://api.origins.heritage.africa:6443 --insecure-skip-tls-verify=true'."
}
OkMsg "oc authentifie en tant que $($who.Stdout)"

& git --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'git introuvable dans le PATH.' }
OkMsg 'git disponible'

$nsCheck = Invoke-Oc -OcArgs @('get','namespace',$Namespace) -AllowFailure
if ($nsCheck.ExitCode -ne 0) {
    throw "Namespace $Namespace introuvable : $($nsCheck.Stderr)"
}
OkMsg "namespace $Namespace OK"

$apiRes = Invoke-Oc -OcArgs @('api-resources','--no-headers') -AllowFailure
if ($apiRes.ExitCode -eq 0) {
    $appMatches = $apiRes.Stdout -split "`n" | Where-Object { $_ -match '\bapp\b' -and $_ -match 'Application' }
    if (($appMatches | Measure-Object).Count -gt 1) {
        WMsg "Collision detectee sur short-name 'app' :"
        $appMatches | ForEach-Object { Write-Host ('         ' + $_.Trim()) -ForegroundColor DarkYellow }
        IMsg "Le script utilise $($Script:ArgoAppFQ) partout pour eviter l'ambiguite."
    }
}

# Resolution chemin absolu du YAML local (utilise pour fallback oc apply)
$LocalYaml = Join-Path $GitOpsRoot $RabbitYamlRel
if (-not (Test-Path $LocalYaml)) {
    throw "Manifest local introuvable : $LocalYaml"
}
OkMsg "manifest local detecte : $LocalYaml"

# ---------------------------------------------------------------------------
# 1. Etat avant fix
# ---------------------------------------------------------------------------
Step '1. Etat actuel pod RabbitMQ (avant fix)'

$before = Invoke-Oc -OcArgs @('get','pod',$PodName,'-n',$Namespace,'-o','wide') -AllowFailure
if ($before.ExitCode -eq 0) {
    Write-Host $before.Stdout
} else {
    IMsg "pod $PodName absent — sera cree par le STS apres sync"
}

# ---------------------------------------------------------------------------
# 2. git add / commit / push
# ---------------------------------------------------------------------------
if (-not $SkipGitPush) {
    Step '2. git add / commit / push (12-rabbitmq.yaml)'

    Push-Location $GitOpsRoot
    try {
        & git add $RabbitYamlRel.Replace('\','/')
        if ($LASTEXITCODE -ne 0) { throw "git add KO (code $LASTEXITCODE)" }

        $staged = (& git diff --cached --name-only) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw "git diff --cached KO (code $LASTEXITCODE)" }

        if ([string]::IsNullOrWhiteSpace($staged)) {
            WMsg 'Rien a commit (deja en HEAD) — on saute commit/push'
        } else {
            IMsg ("staged : {0}" -f $staged)
            & git commit -m $CommitMsg
            if ($LASTEXITCODE -ne 0) { throw "git commit KO (code $LASTEXITCODE)" }
            OkMsg 'commit cree'

            & git push $GitRemote $GitBranch
            if ($LASTEXITCODE -ne 0) { throw "git push KO (code $LASTEXITCODE)" }
            OkMsg "push effectue vers $GitRemote/$GitBranch"
        }
    } finally {
        Pop-Location
    }
} else {
    WMsg '-SkipGitPush : commit/push ignores'
}

# ---------------------------------------------------------------------------
# Helpers etape 3
# ---------------------------------------------------------------------------
function Get-StsHasCookieFix {
    param([string]$Ns, [string]$Sts)
    $obj = Get-OcJson -OcArgs @('get','statefulset',$Sts,'-n',$Ns) -AllowEmpty
    if ($null -eq $obj) { return $false }
    $initList = $obj.spec.template.spec.initContainers
    return ($null -ne $initList -and ($initList | Where-Object { $_.name -eq 'cookie-fix' }))
}

function Invoke-ArgoFallbackApply {
    param([string]$YamlPath, [string]$Ns)
    WMsg 'FALLBACK : oc apply -f direct sur le manifest local (drift Argo a corriger apres).'
    $r = Invoke-Oc -OcArgs @('apply','-f',$YamlPath,'-n',$Ns) -AllowFailure
    Write-Host $r.Stdout
    if ($r.ExitCode -ne 0) {
        throw "oc apply -f $YamlPath KO : $($r.Stderr)"
    }
    OkMsg 'apply direct OK (drift Argo a synchroniser ulterieurement)'
}

# ---------------------------------------------------------------------------
# 3. ArgoCD : discovery + refresh + trigger sync explicite + fallback
# ---------------------------------------------------------------------------
if ($ForceLocalApply) {
    Step '3. Bypass Argo (-ForceLocalApply) -> apply direct'
    Invoke-ArgoFallbackApply -YamlPath $LocalYaml -Ns $Namespace
}
elseif (-not $SkipArgoRefresh) {
    Step '3. ArgoCD : discovery du nom + refresh + trigger sync'

    # 3.a Liste des Applications Argo dans le namespace
    $listed = Invoke-Oc -OcArgs @('get',$Script:ArgoAppFQ,'-n',$ArgoNs,'-o','name') -AllowFailure
    if ($listed.ExitCode -ne 0) {
        throw "Impossible de lister $($Script:ArgoAppFQ) dans $ArgoNs : $($listed.Stderr)"
    }
    $found = @($listed.Stdout -split "`n" | Where-Object { $_ -match '/' } | ForEach-Object { ($_ -split '/')[-1].Trim() } | Where-Object { $_ })
    IMsg ("Applications Argo dans {0} : {1}" -f $ArgoNs, ($found -join ', '))

    if ($found.Count -eq 0) { throw "Aucune Application Argo trouvee dans $ArgoNs." }

    # 3.b Resolution du bon nom (param explicite > defauts > loose 'openimis')
    $resolved = $null
    $candidates = @($ArgoApp) + @('openimis-core','dpg-openimis','openimis','openimis-dev','openimis-app') | Select-Object -Unique
    foreach ($c in $candidates) {
        if ($found -contains $c) { $resolved = $c; break }
    }
    if (-not $resolved) {
        $loose = @($found | Where-Object { $_ -match 'openimis' })
        if ($loose.Count -eq 1) {
            $resolved = $loose[0]
            WMsg "Resolution loose : '$resolved' contient 'openimis'."
        } elseif ($loose.Count -gt 1) {
            throw "Plusieurs Applications Argo contiennent 'openimis' : $($loose -join ', '). Relance avec -ArgoApp <nom-exact>."
        }
    }
    if (-not $resolved) {
        throw "Aucune Application Argo OpenIMIS dans $ArgoNs. Trouvees : $($found -join ', '). Relance avec -ArgoApp <nom-exact>."
    }
    if ($resolved -ne $ArgoApp) { WMsg "Nom Argo resolu : '$resolved' (defaut '$ArgoApp')" }
    OkMsg "Cible Argo : $($Script:ArgoAppFQ)/$resolved -n $ArgoNs"

    # 3.c Diagnostic sync policy
    $appObj = Get-OcJson -OcArgs @('get',$Script:ArgoAppFQ,$resolved,'-n',$ArgoNs) -AllowEmpty
    if ($null -ne $appObj) {
        $auto = $appObj.spec.syncPolicy.automated
        if ($null -eq $auto) {
            WMsg 'syncPolicy = manual -> un refresh ne suffit pas, on va trigger une operation sync explicite.'
        } else {
            IMsg ("syncPolicy.automated present (prune={0}, selfHeal={1}) -> trigger sync explicite quand meme par securite." -f
                  ($(if ($null -ne $auto.prune) { $auto.prune } else { 'null' })),
                  ($(if ($null -ne $auto.selfHeal) { $auto.selfHeal } else { 'null' })))
        }
        $curRev = $appObj.status.sync.revision
        $curStatus = $appObj.status.sync.status
        IMsg ("status actuel : sync.status='{0}', sync.revision='{1}'" -f $curStatus, $curRev)
    }

    # 3.d Refresh hard (invalide le cache, force re-comparaison avec git HEAD)
    $refreshPatch = '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
    $rR = Invoke-Oc -OcArgs @('patch',$Script:ArgoAppFQ,$resolved,'-n',$ArgoNs,'--type','merge','-p',$refreshPatch) -AllowFailure
    if ($rR.ExitCode -ne 0) {
        WMsg "patch refresh KO ($($rR.Stderr)) — on continue vers trigger sync direct"
    } else {
        OkMsg 'refresh hard demande (cache Argo invalide)'
    }
    Start-Sleep -Seconds 5  # laisse Argo recharger l'app et recomparer

    # 3.e Trigger explicite operation.sync (force, prune off pour iter 1)
    $syncPatch = '{"operation":{"initiatedBy":{"username":"fix-rabbitmq-script"},"sync":{"revision":"HEAD","prune":false,"syncOptions":["ApplyOutOfSyncOnly=false","CreateNamespace=true","ServerSideApply=true"]}}}'
    $rs1 = Invoke-Oc -OcArgs @('patch',$Script:ArgoAppFQ,$resolved,'-n',$ArgoNs,'--type','merge','-p',$syncPatch) -AllowFailure
    if ($rs1.ExitCode -ne 0) {
        WMsg "trigger sync 1 KO ($($rs1.Stderr)) — backoff 8s puis retry"
        Start-Sleep -Seconds 8
        $rs2 = Invoke-Oc -OcArgs @('patch',$Script:ArgoAppFQ,$resolved,'-n',$ArgoNs,'--type','merge','-p',$syncPatch) -AllowFailure
        if ($rs2.ExitCode -ne 0) {
            WMsg "trigger sync 2 KO ($($rs2.Stderr)) — bascule fallback apply direct"
            Invoke-ArgoFallbackApply -YamlPath $LocalYaml -Ns $Namespace
        } else {
            OkMsg 'trigger sync explicite OK (retry 2)'
        }
    } else {
        OkMsg 'trigger sync explicite OK'
    }

    # 3.f Attente : (sync.status == Synced) ET (STS contient cookie-fix)
    Step ("3b. Attente sync + reflect STS (max {0}s)" -f $SyncTimeoutSec)
    $deadline = (Get-Date).AddSeconds($SyncTimeoutSec)
    $syncedApp = $false
    $stsOk     = $false
    $lastStatus = ''
    $lastPhase  = ''
    while ((Get-Date) -lt $deadline) {
        $appObj2 = Get-OcJson -OcArgs @('get',$Script:ArgoAppFQ,$resolved,'-n',$ArgoNs) -AllowEmpty
        if ($null -ne $appObj2) {
            $lastStatus = if ($appObj2.status.sync.status) { $appObj2.status.sync.status } else { '?' }
            $lastPhase  = if ($appObj2.status.operationState -and $appObj2.status.operationState.phase) { $appObj2.status.operationState.phase } else { '-' }
            if ($lastStatus -eq 'Synced') { $syncedApp = $true }
        }
        $stsOk = Get-StsHasCookieFix -Ns $Namespace -Sts $StsName
        if ($syncedApp -and $stsOk) { break }
        Start-Sleep -Seconds 5
        Write-Host ('.[{0}/{1}]' -f $lastStatus, $lastPhase) -NoNewline
    }
    Write-Host ''

    if ($syncedApp -and $stsOk) {
        OkMsg "Argo Synced + STS contient initContainer cookie-fix (phase op='$lastPhase')"
    } else {
        WMsg ("Etat final : sync.status='{0}' operationState.phase='{1}' STS-has-cookie-fix={2}" -f $lastStatus, $lastPhase, $stsOk)

        # Dump diagnostique court de l'application
        $appObjD = Get-OcJson -OcArgs @('get',$Script:ArgoAppFQ,$resolved,'-n',$ArgoNs) -AllowEmpty
        if ($null -ne $appObjD -and $appObjD.status) {
            Write-Host '--- dump status (sync, operationState.phase/message, conditions) ---' -ForegroundColor DarkGray
            if ($appObjD.status.sync) {
                Write-Host ('  sync.status     : {0}' -f $appObjD.status.sync.status)
                Write-Host ('  sync.revision   : {0}' -f $appObjD.status.sync.revision)
            }
            if ($appObjD.status.operationState) {
                Write-Host ('  op.phase        : {0}' -f $appObjD.status.operationState.phase)
                Write-Host ('  op.message      : {0}' -f $appObjD.status.operationState.message)
                Write-Host ('  op.finishedAt   : {0}' -f $appObjD.status.operationState.finishedAt)
            }
            if ($appObjD.status.conditions) {
                foreach ($c in $appObjD.status.conditions) {
                    Write-Host ('  condition[{0}]  : {1}' -f $c.type, $c.message)
                }
            }
            Write-Host '-------------------------------------------------------------------' -ForegroundColor DarkGray
        }

        # Sync OK cote Argo mais STS non a jour -> probleme cote kustomize/contenu
        if ($syncedApp -and -not $stsOk) {
            WMsg 'Argo dit Synced mais STS sans initContainer cookie-fix : le manifest commit ne contient peut-etre pas le patch attendu.'
            WMsg 'Verification rapide locale :'
            $grep = Select-String -Path $LocalYaml -Pattern 'cookie-fix' -SimpleMatch -CaseSensitive
            if ($grep) {
                OkMsg ("OK : 'cookie-fix' present dans {0} (ligne {1})" -f $LocalYaml, $grep.LineNumber)
                IMsg 'Bascule fallback apply direct pour debloquer.'
                Invoke-ArgoFallbackApply -YamlPath $LocalYaml -Ns $Namespace
            } else {
                throw "Manifest local $LocalYaml ne contient PAS 'cookie-fix' -> le patch YAML a ete perdu. Re-applique le fix initial."
            }
        }
        # Argo pas sync -> fallback apply direct
        elseif (-not $syncedApp) {
            WMsg 'Argo pas Synced dans le delai -> bascule fallback apply direct'
            Invoke-ArgoFallbackApply -YamlPath $LocalYaml -Ns $Namespace
        }

        # Confirmation finale
        if (-not (Get-StsHasCookieFix -Ns $Namespace -Sts $StsName)) {
            throw "Apres fallback, STS $StsName ne contient toujours pas cookie-fix. Abandon."
        }
        OkMsg 'STS contient maintenant initContainer cookie-fix (via fallback)'
    }
} else {
    WMsg '-SkipArgoRefresh : refresh + trigger sync ignores'
}

# ---------------------------------------------------------------------------
# 4. Wipe PVC (optionnel) ou force-delete pod
# ---------------------------------------------------------------------------
if ($WipePvc) {
    Step '4. WIPE PVC (donnees broker effacees)'
    WMsg 'ATTENTION : suppression des queues / messages persistents'

    Invoke-Oc -OcArgs @('scale','statefulset',$StsName,'-n',$Namespace,'--replicas=0') | Out-Null
    OkMsg "STS $StsName scale a 0"

    $wd = Invoke-Oc -OcArgs @('wait','--for=delete','pod',$PodName,'-n',$Namespace,'--timeout=90s') -AllowFailure
    if ($wd.ExitCode -ne 0) { WMsg "wait delete pod : $($wd.Stderr)" }

    Invoke-Oc -OcArgs @('delete','pvc',$PvcName,'-n',$Namespace,'--ignore-not-found','--wait=true','--timeout=120s') | Out-Null
    OkMsg "PVC $PvcName supprime"

    Invoke-Oc -OcArgs @('scale','statefulset',$StsName,'-n',$Namespace,'--replicas=1') | Out-Null
    OkMsg "STS $StsName scale a 1 (PVC sera recree par le volumeClaimTemplate)"
} else {
    Step '4. Force-delete pod (initContainer corrigera le cookie sur place)'
    Invoke-Oc -OcArgs @('delete','pod',$PodName,'-n',$Namespace,'--grace-period=0','--force','--ignore-not-found') | Out-Null
    OkMsg "pod $PodName tue, STS recree immediatement"
}

# ---------------------------------------------------------------------------
# 5. Attente terminaison initContainer cookie-fix
# ---------------------------------------------------------------------------
Step '5. Attente terminaison initContainer cookie-fix (max 180s)'
$deadline = (Get-Date).AddSeconds(180)
$initOk = $false
while ((Get-Date) -lt $deadline) {
    $pod = Get-OcJson -OcArgs @('get','pod',$PodName,'-n',$Namespace) -AllowEmpty
    if ($null -ne $pod -and $pod.status.initContainerStatuses) {
        $init = $pod.status.initContainerStatuses | Where-Object { $_.name -eq 'cookie-fix' }
        if ($init) {
            if ($init.state.terminated -and $init.state.terminated.reason -eq 'Completed') {
                $initOk = $true
                break
            }
            if ($init.state.waiting -and $init.state.waiting.reason -eq 'CrashLoopBackOff') {
                throw "initContainer cookie-fix en CrashLoopBackOff : $($init.state.waiting.message)"
            }
        }
    }
    Start-Sleep -Seconds 4
    Write-Host '.' -NoNewline
}
Write-Host ''

if ($initOk) {
    OkMsg 'cookie-fix termine avec exit 0'
} else {
    WMsg 'cookie-fix pas termine dans le delai — affichage des logs partiels'
}

$initLogs = Invoke-Oc -OcArgs @('logs',$PodName,'-c','cookie-fix','-n',$Namespace) -AllowFailure
Write-Host '--- logs cookie-fix ---' -ForegroundColor DarkGray
Write-Host $initLogs.Stdout
Write-Host '-----------------------' -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# 6. Verif frontend (post-patch nodeSelector)
# ---------------------------------------------------------------------------
Step '6. Verif frontend reschedule (apres remove nodeSelector)'
$front = Invoke-Oc -OcArgs @('get','pods','-n',$Namespace,'-l','app.kubernetes.io/component=frontend','-o','wide') -AllowFailure
if ($front.ExitCode -eq 0) {
    Write-Host $front.Stdout
    if ($front.Stdout -match 'Pending') {
        WMsg 'frontend encore Pending — describe pour voir les events :'
        $fpods = Get-OcJson -OcArgs @('get','pods','-n',$Namespace,'-l','app.kubernetes.io/component=frontend') -AllowEmpty
        if ($fpods -and $fpods.items) {
            foreach ($p in $fpods.items) {
                if ($p.status.phase -eq 'Pending') {
                    Write-Host ("--- describe {0} ---" -f $p.metadata.name) -ForegroundColor DarkGray
                    $desc = Invoke-Oc -OcArgs @('describe','pod',$p.metadata.name,'-n',$Namespace) -AllowFailure
                    $lines  = $desc.Stdout -split "`n"
                    $start  = ($lines | Select-String '^Events:').LineNumber
                    if ($start) {
                        $lines[($start - 1)..($lines.Count - 1)] | ForEach-Object { Write-Host $_ }
                    } else {
                        Write-Host $desc.Stdout
                    }
                }
            }
        }
    } else {
        OkMsg 'frontend Running — reschedule reussi'
    }
} else {
    WMsg "get pods frontend KO : $($front.Stderr)"
}

# ---------------------------------------------------------------------------
# 7. Tail logs rabbitmq (streaming, bloquant)
# ---------------------------------------------------------------------------
Step '7. Tail logs RabbitMQ (Ctrl+C pour quitter)'
IMsg 'A surveiller :'
IMsg '  - "Cookie file ..."           -> NE DOIT PLUS APPARAITRE'
IMsg '  - "started TCP listener on [::]:5672"'
IMsg '  - "Server startup complete; N plugins started."'
IMsg ''

& oc logs $PodName -n $Namespace --tail=$LogTailLines -f --insecure-skip-tls-verify=true
