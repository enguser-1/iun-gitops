# ASTREINTE Heritage - diagnostic NooBaa Origins (LECTURE SEULE)
# Objectif : comprendre pourquoi noobaa-default-backing-store (pv-pool) est Rejected
# et pourquoi les objets ecrits depuis le ~18/07 sont illisibles.
$reportDir = "C:\IUN_APP\cowork\reports"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $reportDir "noobaa-diag_$stamp.log"
function W($m) { $line = "[$(Get-Date -Format 'HH:mm:ss')] $m"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }
function Sect($t) { W ""; W "=== $t ===" }
$ns = "openshift-storage"

W "=== DIAGNOSTIC NooBaa Origins (lecture seule) ==="
$probe = (oc get ns iun-opencrvs-dev -o name 2>&1 | Out-String).Trim()
if ($probe -notmatch '^namespace/iun-opencrvs-dev$') {
  Write-Host "Token Origins invalide. Colle la commande oc login d'ORIGINS :"
  $loginCmd = Read-Host "oc login Origins"
  if ($loginCmd -notmatch '^oc login ' -or $loginCmd -match ';|\||&' -or $loginCmd -match 'l1\.') { W "FATAL: commande invalide"; exit 5 }
  Invoke-Expression ($loginCmd + " --insecure-skip-tls-verify=true") 2>&1 | ForEach-Object { W "  $_" }
}
W "connecte : $((oc whoami 2>&1 | Out-String).Trim()) sur $((oc whoami --show-server 2>&1 | Out-String).Trim())"

Sect "backingstore : spec + conditions completes"
oc get backingstore noobaa-default-backing-store -n $ns -o jsonpath='{.spec}' 2>&1 | ForEach-Object { W "  spec: $_" }
W ""
$conds = oc get backingstore noobaa-default-backing-store -n $ns -o jsonpath='{range .status.conditions[*]}{.type}{"|"}{.status}{"|"}{.reason}{"|"}{.message}{"\n"}{end}' 2>&1 | Out-String
foreach ($ln in ($conds -split "`r?`n")) { if ($ln.Trim()) { W "  cond: $ln" } }
$mode = oc get backingstore noobaa-default-backing-store -n $ns -o jsonpath='{.status.mode}' 2>&1 | Out-String
W "  mode: $($mode.Trim())"

Sect "describe backingstore (events inclus)"
$desc = oc describe backingstore noobaa-default-backing-store -n $ns 2>&1 | Out-String
foreach ($ln in ($desc -split "`r?`n")) { if ($ln -match 'Mode|Phase|Reason|Message|Type:|Status:|Warning|Error|Event') { W "  $ln" } }

Sect "PVC noobaa (capacite du pv-pool)"
$pvcs = oc get pvc -n $ns 2>&1 | Out-String
foreach ($ln in ($pvcs -split "`r?`n")) { if ($ln -match 'NAME|noobaa') { W "  $ln" } }

Sect "pod pv-pool : etat, restarts, derniere sortie"
$pods = oc get pods -n $ns --no-headers 2>&1 | Out-String
$bsPod = ""
foreach ($ln in ($pods -split "`r?`n")) {
  if ($ln -match 'noobaa') { W "  $ln" }
  if ($ln -match 'noobaa-default-backing-store') { $bsPod = ($ln -split '\s+')[0] }
}
if ($bsPod) {
  W "  --- describe $bsPod ---"
  $d = oc describe pod $bsPod -n $ns 2>&1 | Out-String
  $keep = $false
  foreach ($ln in ($d -split "`r?`n")) {
    if ($ln -match 'State:|Last State:|Reason:|Exit Code:|Restart Count:|Started:|Finished:|OOM|Evict') { W "  $ln" }
    if ($ln -match '^Events:') { $keep = $true }
    if ($keep -and $ln.Trim()) { W "  ev $ln" }
  }
  W "  --- df dans le pod pv-pool ---"
  oc exec $bsPod -n $ns -- df -h 2>&1 | ForEach-Object { W "    $_" }
  W "  --- logs agent pv-pool (tail 30) ---"
  oc logs $bsPod -n $ns --tail=30 2>&1 | ForEach-Object { W "    ag: $_" }
  $prev = oc logs $bsPod -n $ns --previous --tail=20 2>&1 | Out-String
  if ($prev -notmatch 'unable to retrieve|not found') {
    W "  --- logs PRECEDENT crash (tail 20) ---"
    foreach ($ln in ($prev -split "`r?`n")) { if ($ln.Trim()) { W "    pv: $ln" } }
  }
}

Sect "logs noobaa-core (erreurs recentes)"
$core = oc logs noobaa-core-0 -n $ns --tail=200 2>&1 | Out-String
$errCount = 0
foreach ($ln in ($core -split "`r?`n")) {
  if ($ln -match 'ERROR|WARN.*(chunk|block|agent|pool|rebuild|verify)' -and $errCount -lt 30) { W "  core: $ln"; $errCount++ }
}
if ($errCount -eq 0) { W "  (pas d'erreur evidente dans les 200 dernieres lignes)" }

Sect "logs noobaa-endpoint (echecs GET recents)"
$epPod = ""
foreach ($ln in ($pods -split "`r?`n")) { if ($ln -match 'noobaa-endpoint') { $epPod = ($ln -split '\s+')[0] } }
if ($epPod) {
  $ep = oc logs $epPod -n $ns --tail=200 2>&1 | Out-String
  $c2 = 0
  foreach ($ln in ($ep -split "`r?`n")) {
    if ($ln -match 'ERROR|read_object|chunk|block.*(fail|error|missing)' -and $c2 -lt 30) { W "  ep: $ln"; $c2++ }
  }
  if ($c2 -eq 0) { W "  (pas d'erreur evidente)" }
}

Sect "CR noobaa : conditions"
$nb = oc get noobaa noobaa -n $ns -o jsonpath='{range .status.conditions[*]}{.type}{"|"}{.status}{"|"}{.reason}{"\n"}{end}' 2>&1 | Out-String
foreach ($ln in ($nb -split "`r?`n")) { if ($ln.Trim()) { W "  $ln" } }

Sect "bucketclasses (qui pointe sur le backingstore malade)"
oc get bucketclass -n $ns 2>&1 | ForEach-Object { W "  $_" }
oc get bucketclass noobaa-default-bucket-class -n $ns -o jsonpath='{.spec}' 2>&1 | ForEach-Object { W "  default-bc spec: $_" }

Sect "events recents du namespace (25 derniers)"
$evs = oc get events -n $ns --sort-by=.lastTimestamp 2>&1 | Out-String
($evs -split "`r?`n") | Select-Object -Last 25 | ForEach-Object { if ($_.Trim()) { W "  $_" } }

Sect "PV du pv-pool (etat)"
$pvList = oc get pv 2>&1 | Out-String
foreach ($ln in ($pvList -split "`r?`n")) { if ($ln -match 'NAME|noobaa') { W "  $ln" } }

Copy-Item -Force -Path $log -Destination (Join-Path $reportDir "noobaa-diag-latest.log")
W ""
W "=== DONE (lecture seule) - remediation decidee sur la base de ce rapport ==="
