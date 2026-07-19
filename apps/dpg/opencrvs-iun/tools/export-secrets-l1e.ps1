# Export chiffre v5 (final robuste) : UN pod, DEUX conteneurs, volume partage.
#   crypt (alpine/openssl) : recoit le plaintext(b64) en stdin -> ecrit /shared/secrets.enc
#   upload (aws-cli)       : lit /shared/secrets.enc -> L1  (aucun chiffre ne transite par PS)
$reportDir = "C:\IUN_APP\cowork\reports"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $reportDir "export-secrets5_$stamp.log"
function W($m) { $line = "[$(Get-Date -Format 'HH:mm:ss')] $m"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$ascii = [System.Text.Encoding]::ASCII
$nsO = "iun-opencrvs-dev"

W "=== export chiffre v5 (pod 2 conteneurs, volume partage) -> L1 ==="
$probe = (oc get ns $nsO -o name 2>&1 | Out-String).Trim()
if ($probe -notmatch '^namespace/iun-opencrvs-dev$') {
  Write-Host "Token Origins invalide. Colle la commande oc login d'ORIGINS :"
  $loginCmd = Read-Host "oc login Origins"
  if ($loginCmd -notmatch '^oc login ' -or $loginCmd -match ';|\||&' -or $loginCmd -match 'l1\.') { W "FATAL: commande invalide"; exit 5 }
  Invoke-Expression ($loginCmd + " --insecure-skip-tls-verify=true") 2>&1 | ForEach-Object { W "  $_" }
}

Write-Host ""
Write-Host "Passphrase de chiffrement (>= 10 car., NON stockee). Note-la en lieu sur."
$ss1 = Read-Host "Passphrase" -AsSecureString
$ss2 = Read-Host "Confirme" -AsSecureString
function SsToPlain($ss) { $b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss); try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) } }
$pp1 = SsToPlain $ss1; $pp2 = SsToPlain $ss2
if ($pp1 -ne $pp2) { W "FATAL: passphrases differentes"; exit 6 }
if ($pp1.Length -lt 10) { W "FATAL: passphrase trop courte"; exit 7 }
$ppB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($pp1))
$pp1 = $null; $pp2 = $null
W "passphrase acceptee"

$exclTypes = @("kubernetes.io/service-account-token","kubernetes.io/dockercfg","kubernetes.io/dockerconfigjson","helm.sh/release.v1")
$sb = New-Object System.Text.StringBuilder
$total = 0
foreach ($n in @("iun-opencrvs-dev","iun-openimis-dev","iun-mosip-dev")) {
  $js = oc get secret -n $n -o json 2>&1 | Out-String | ConvertFrom-Json
  foreach ($s in $js.items) {
    if ($exclTypes -contains $s.type) { continue }
    if ($s.metadata.name -match '^(default|builder|deployer)-|-dockercfg|-token-') { continue }
    if ($s.metadata.name -eq "dr-export-pass") { continue }
    $y = oc get secret $s.metadata.name -n $n -o yaml 2>&1 | Out-String
    [void]$sb.AppendLine("# === $n / $($s.metadata.name) ($($s.type)) ===")
    [void]$sb.AppendLine($y.TrimEnd()); [void]$sb.AppendLine("---")
    $total++
  }
}
W "secrets collectes : $total"
if ($total -lt 20) { W "FATAL: trop peu de secrets"; exit 8 }
$plainFile = "$env:TEMP\iun-plain.b64"
[System.IO.File]::WriteAllText($plainFile, [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($sb.ToString())), $ascii)
$sb.Clear() | Out-Null

$passSecretYaml = "apiVersion: v1`nkind: Secret`nmetadata:`n  name: dr-export-pass`n  namespace: $nsO`ntype: Opaque`ndata:`n  PASS: $ppB64`n"
$ppB64 = $null
$passSecretYaml | oc apply -f - 2>&1 | ForEach-Object { W "  $_" }

# --- pod 2 conteneurs + emptyDir partage ---
$pod = @{
  apiVersion = "v1"; kind = "Pod"
  metadata = @{ name = "dr-crypt"; namespace = $nsO; labels = @{ "iun.sn/backup" = "true" } }
  spec = @{
    restartPolicy = "Never"
    volumes = @(@{ name = "shared"; emptyDir = @{} })
    containers = @(
      @{ name = "crypt"; image = "docker.io/alpine/openssl:latest"
         env = @(@{ name = "PASS"; valueFrom = @{ secretKeyRef = @{ name = "dr-export-pass"; key = "PASS" } } })
         volumeMounts = @(@{ name = "shared"; mountPath = "/shared" })
         command = @("sh","-c","sleep 900") },
      @{ name = "upload"; image = "amazon/aws-cli:2.17.16"
         envFrom = @(@{ secretRef = @{ name = "iun-dr-s3" } })
         volumeMounts = @(@{ name = "shared"; mountPath = "/shared" })
         command = @("sh","-c","sleep 900") }
    )
  }
}
oc delete pod dr-crypt -n $nsO --ignore-not-found 2>&1 | Out-Null
Start-Sleep -Seconds 3
$pf = "$env:TEMP\dr-crypt5.json"
[System.IO.File]::WriteAllText($pf, ($pod | ConvertTo-Json -Depth 20), $utf8NoBom)
oc apply -f $pf 2>&1 | Out-Null
$ready = $false
foreach ($i in 1..45) {
  $rc = (oc get pod dr-crypt -n $nsO -o jsonpath='{.status.containerStatuses[*].ready}' 2>&1 | Out-String).Trim()
  if ($rc -match 'true true') { $ready = $true; break }
  Start-Sleep -Seconds 4
}
if (-not $ready) { W "FATAL: conteneurs pas prets"; oc delete secret dr-export-pass -n $nsO --ignore-not-found 2>&1 | Out-Null; exit 9 }
W "openssl : $((oc exec dr-crypt -n $nsO -c crypt -- openssl version 2>&1 | Out-String).Trim())"

# --- 1) chiffrement -> /shared/secrets.enc (stdin plaintext b64 = chemin qui marche) ---
$encCmd = 'base64 -d | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:PASS -out /shared/secrets.enc 2>/dev/null; echo "octets chiffres: $(stat -c %s /shared/secrets.enc 2>/dev/null || wc -c < /shared/secrets.enc)"'
$outE = Get-Content -Raw $plainFile | oc exec -i dr-crypt -n $nsO -c crypt -- sh -c $encCmd 2>&1 | Out-String
Remove-Item -Force $plainFile -ErrorAction SilentlyContinue
foreach ($ln in ($outE -split "`r?`n")) { if ($ln.Trim()) { W "  $ln" } }

# --- 2) upload depuis le conteneur aws-cli (lit le volume partage) ---
$upCmd = 'ls -l /shared/secrets.enc; AWS_ACCESS_KEY_ID="$L1_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$L1_SECRET_KEY" aws --endpoint-url "$L1_S3_ENDPOINT" --no-verify-ssl s3 cp /shared/secrets.enc "s3://$L1_BUCKET/secrets/iun-secrets.enc" 2>/dev/null && echo ENC-UPLOAD-OK'
$outU = oc exec dr-crypt -n $nsO -c upload -- sh -c $upCmd 2>&1 | Out-String
foreach ($ln in ($outU -split "`r?`n")) { if ($ln.Trim()) { W "  $ln" } }

# --- 3) verif : taille L1 + dechiffrement du /shared ---
$sizeL1 = (oc exec dr-crypt -n $nsO -c upload -- sh -c 'AWS_ACCESS_KEY_ID="$L1_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$L1_SECRET_KEY" aws --endpoint-url "$L1_S3_ENDPOINT" --no-verify-ssl s3api head-object --bucket "$L1_BUCKET" --key secrets/iun-secrets.enc --query ContentLength --output text 2>/dev/null' 2>$null | Out-String).Trim()
W "taille objet L1 : $sizeL1 octets"
$cnt = (oc exec dr-crypt -n $nsO -c crypt -- sh -c 'openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:PASS -in /shared/secrets.enc 2>/dev/null | grep -c "^kind: Secret"' 2>&1 | Out-String).Trim()
W "verification dechiffrement : $cnt secrets (attendu $total)"

oc delete pod dr-crypt -n $nsO --ignore-not-found 2>&1 | Out-Null
oc delete secret dr-export-pass -n $nsO --ignore-not-found 2>&1 | ForEach-Object { W "  $_" }

Copy-Item -Force -Path $log -Destination (Join-Path $reportDir "export-secrets5-latest.log")
W ""
W "=== DONE - secrets/iun-secrets.enc sur L1 ($cnt secrets) ==="
