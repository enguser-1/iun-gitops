# ASTREINTE Heritage - remediation NooBaa Origins
# Cause : agent pv-pool OOMKilled le 17/07 puis heartbeats en timeout -> ALL_NODES_OFFLINE -> Rejected.
# Plan (du moins au plus invasif) :
#  A) restart de l'agent pv-pool -> attendre le retour du backingstore
#  B) si toujours Rejected : restart noobaa-core puis endpoint (coupure S3 ~1 min, deja degrade)
#  C) verification ecriture+lecture reelle sur un bucket
#  D) prevention : limite memoire de l'agent relevee (l'OOM est le declencheur racine)
$reportDir = "C:\IUN_APP\cowork\reports"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $reportDir "noobaa-fix_$stamp.log"
function W($m) { $line = "[$(Get-Date -Format 'HH:mm:ss')] $m"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$ascii = [System.Text.Encoding]::ASCII
$ns = "openshift-storage"
$nsO = "iun-opencrvs-dev"

function BackingPhase() {
  return (oc get backingstore noobaa-default-backing-store -n $ns -o jsonpath='{.status.phase}' 2>&1 | Out-String).Trim()
}
function BackingMode() {
  return (oc get backingstore noobaa-default-backing-store -n $ns -o jsonpath='{.status.mode.modeCode}' 2>&1 | Out-String).Trim()
}
function WaitPhase($targetSeconds) {
  $d = (Get-Date).AddSeconds($targetSeconds)
  while ((Get-Date) -lt $d) {
    Start-Sleep -Seconds 15
    $p = BackingPhase
    $m = BackingMode
    W "  phase=$p mode=$m"
    if ($p -eq "Ready" -and $m -notmatch 'OFFLINE') { return $true }
  }
  return $false
}

W "=== remediation NooBaa Origins ==="
$probe = (oc get ns $nsO -o name 2>&1 | Out-String).Trim()
if ($probe -notmatch '^namespace/iun-opencrvs-dev$') {
  Write-Host "Token Origins invalide. Colle la commande oc login d'ORIGINS :"
  $loginCmd = Read-Host "oc login Origins"
  if ($loginCmd -notmatch '^oc login ' -or $loginCmd -match ';|\||&' -or $loginCmd -match 'l1\.') { W "FATAL: commande invalide"; exit 5 }
  Invoke-Expression ($loginCmd + " --insecure-skip-tls-verify=true") 2>&1 | ForEach-Object { W "  $_" }
}
W "connecte : $((oc whoami 2>&1 | Out-String).Trim())"
W "etat initial : phase=$(BackingPhase) mode=$(BackingMode)"

# ---------- PHASE A : restart de l'agent pv-pool ----------
W ""
W "=== PHASE A : restart de l'agent pv-pool ==="
$agentPod = ((oc get pods -n $ns --no-headers 2>&1 | Out-String) -split "`r?`n" | Where-Object { $_ -match 'noobaa-default-backing-store' } | Select-Object -First 1)
if ($agentPod) {
  $ap = ($agentPod -split '\s+')[0]
  oc delete pod $ap -n $ns 2>&1 | ForEach-Object { W "  $_" }
  # attendre la recreation par l'operator
  $recreated = $false
  foreach ($i in 1..40) {
    Start-Sleep -Seconds 6
    $np = ((oc get pods -n $ns --no-headers 2>&1 | Out-String) -split "`r?`n" | Where-Object { $_ -match 'noobaa-default-backing-store' -and $_ -match 'Running' } | Select-Object -First 1)
    if ($np -and (($np -split '\s+')[0]) -ne $ap) { $recreated = $true; W "  nouveau pod agent : $np"; break }
    if ($np -and $i -gt 20) { $recreated = $true; W "  pod agent : $np"; break }
  }
  if (-not $recreated) { W "  agent pas recree/Running apres 4 min" }
}
W "attente du retour du backingstore (4 min max)..."
$okA = WaitPhase 240
if ($okA) { W "PHASE A SUFFISANTE : backingstore Ready" }

# ---------- PHASE B : restart noobaa-core puis endpoint ----------
if (-not $okA) {
  W ""
  W "=== PHASE B : restart noobaa-core-0 (mgmt) puis endpoint ==="
  oc delete pod noobaa-core-0 -n $ns 2>&1 | ForEach-Object { W "  $_" }
  $coreOk = $false
  foreach ($i in 1..40) {
    Start-Sleep -Seconds 8
    $st = (oc get pod noobaa-core-0 -n $ns -o jsonpath='{.status.phase}' 2>&1 | Out-String).Trim()
    if ($st -eq "Running") {
      $ready = (oc get pod noobaa-core-0 -n $ns -o jsonpath='{.status.containerStatuses[0].ready}' 2>&1 | Out-String).Trim()
      if ($ready -eq "true") { $coreOk = $true; break }
    }
  }
  W "  noobaa-core-0 ready : $coreOk"
  $epPod = ((oc get pods -n $ns --no-headers 2>&1 | Out-String) -split "`r?`n" | Where-Object { $_ -match 'noobaa-endpoint' } | Select-Object -First 1)
  if ($epPod) {
    oc delete pod (($epPod -split '\s+')[0]) -n $ns 2>&1 | ForEach-Object { W "  $_" }
  }
  Start-Sleep -Seconds 20
  # re-restart de l'agent pour forcer une reconnexion propre au nouveau core
  $agentPod2 = ((oc get pods -n $ns --no-headers 2>&1 | Out-String) -split "`r?`n" | Where-Object { $_ -match 'noobaa-default-backing-store' } | Select-Object -First 1)
  if ($agentPod2) { oc delete pod (($agentPod2 -split '\s+')[0]) -n $ns 2>&1 | ForEach-Object { W "  $_" } }
  W "attente du retour du backingstore (6 min max)..."
  $okB = WaitPhase 360
  if ($okB) { W "PHASE B : backingstore Ready" } else { W "PHASE B INSUFFISANTE : backingstore toujours KO - arret, escalade necessaire (voir log)" }
}

# ---------- PHASE C : verification ecriture + lecture reelles ----------
$phaseNow = BackingPhase
if ($phaseNow -eq "Ready") {
  W ""
  W "=== PHASE C : test ecriture/lecture sur le bucket iun-backups ==="
  $testSh = @'
EPL=http://s3.openshift-storage.svc
echo "test-noobaa-$(date +%s)" > /tmp/canary.txt
if aws --endpoint-url $EPL s3 cp /tmp/canary.txt "s3://$BUCKET_NAME/canary/canary.txt"; then echo PUT-OK; else echo PUT-FAIL; fi
if aws --endpoint-url $EPL s3 cp "s3://$BUCKET_NAME/canary/canary.txt" /tmp/canary-back.txt; then echo GET-OK; else echo GET-FAIL; fi
cmp -s /tmp/canary.txt /tmp/canary-back.txt && echo CONTENU-IDENTIQUE
aws --endpoint-url $EPL s3 rm "s3://$BUCKET_NAME/canary/canary.txt" >/dev/null 2>&1
echo "== relecture des objets 18-19/07 (probablement perdus) =="
for k in $(aws --endpoint-url $EPL s3api list-objects-v2 --bucket "$BUCKET_NAME" --query 'Contents[].Key' --output text); do
  case "$k" in */) continue;; esac
  if aws --endpoint-url $EPL s3 cp "s3://$BUCKET_NAME/$k" /dev/null >/dev/null 2>&1; then echo "LISIBLE    $k"; else echo "ILLISIBLE  $k"; fi
done
echo FIN-TEST
'@
  $testFile = "$env:TEMP\canary.sh"
  [System.IO.File]::WriteAllText($testFile, ($testSh -replace "`r`n","`n"), $ascii)
  $shellPod = @{
    apiVersion = "v1"; kind = "Pod"
    metadata = @{ name = "dr-shell"; namespace = $nsO; labels = @{ "iun.sn/backup" = "true" } }
    spec = @{
      restartPolicy = "Never"
      containers = @(@{
        name = "shell"; image = "amazon/aws-cli:2.17.16"
        envFrom = @(@{ secretRef = @{ name = "iun-backups" } }, @{ configMapRef = @{ name = "iun-backups" } })
        command = @("sh","-c","sleep 600")
      })
    }
  }
  oc delete pod dr-shell -n $nsO --ignore-not-found 2>&1 | Out-Null
  Start-Sleep -Seconds 3
  $spF = "$env:TEMP\dr-shell6.json"
  [System.IO.File]::WriteAllText($spF, ($shellPod | ConvertTo-Json -Depth 15), $utf8NoBom)
  oc apply -f $spF 2>&1 | Out-Null
  foreach ($i in 1..30) {
    $phP = (oc get pod dr-shell -n $nsO -o jsonpath='{.status.phase}' 2>&1 | Out-String).Trim()
    if ($phP -eq "Running") { break }
    Start-Sleep -Seconds 4
  }
  $outT = Get-Content -Raw $testFile | oc exec -i dr-shell -n $nsO -- sh 2>&1 | Out-String
  foreach ($ln in ($outT -split "`r?`n")) { if ($ln.Trim()) { W "  $ln" } }
  oc delete pod dr-shell -n $nsO --ignore-not-found 2>&1 | Out-Null

  # ---------- PHASE D : prevention - memoire de l'agent ----------
  W ""
  W "=== PHASE D : limite memoire agent pv-pool (prevention OOM) ==="
  $patch = '{"spec":{"pvPool":{"resources":{"requests":{"storage":"50Gi","memory":"600Mi","cpu":"100m"},"limits":{"memory":"1Gi","cpu":"1"}}}}}'
  oc patch backingstore noobaa-default-backing-store -n $ns --type=merge -p $patch 2>&1 | ForEach-Object { W "  $_" }
  W "  (l'operator peut recreer le pod agent - phase transitoire normale)"
  Start-Sleep -Seconds 30
  W "etat final : phase=$(BackingPhase) mode=$(BackingMode)"
} else {
  W ""
  W "backingstore pas Ready - phases C/D sautees. Escalade : support Red Hat ODF (must-gather) recommande."
}

Copy-Item -Force -Path $log -Destination (Join-Path $reportDir "noobaa-fix-latest.log")
W ""
W "=== DONE ==="
