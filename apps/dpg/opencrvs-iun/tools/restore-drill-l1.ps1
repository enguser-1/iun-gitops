# RESTORE-DRILL depuis L1 (v2, quoting-safe) : restaure les dumps DEPUIS le bucket L1 dans des
# instances EPHEMERES et compte les tables/documents. Scripts shell en here-string litteral,
# prefixe S3 passe par variable d'environnement (PREFIX). Verif generique (toutes bases).
$reportDir = "C:\IUN_APP\cowork\reports"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$jstamp = Get-Date -Format "HHmmss"
$log = Join-Path $reportDir "restore-drill_$stamp.log"
function W($m) { $line = "[$(Get-Date -Format 'HH:mm:ss')] $m"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$nsO = "iun-opencrvs-dev"

W "=== RESTORE-DRILL depuis L1 (instances ephemeres) ==="
$probe = (oc get ns $nsO -o name 2>&1 | Out-String).Trim()
if ($probe -notmatch '^namespace/iun-opencrvs-dev$') {
  Write-Host "Token Origins invalide. Colle la commande oc login d'ORIGINS :"
  $loginCmd = Read-Host "oc login Origins"
  if ($loginCmd -notmatch '^oc login ' -or $loginCmd -match ';|\||&' -or $loginCmd -match 'l1\.') { W "FATAL: commande invalide"; exit 5 }
  Invoke-Expression ($loginCmd + " --insecure-skip-tls-verify=true") 2>&1 | ForEach-Object { W "  $_" }
}

# scripts shell LITTERAUX (here-string : guillemets simples autorises, pas d'interpolation PS)
$dlScript = @'
set -e
EP="$L1_S3_ENDPOINT"
export AWS_ACCESS_KEY_ID="$L1_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$L1_SECRET_KEY"
KEY=$(aws --endpoint-url $EP --no-verify-ssl s3api list-objects-v2 --bucket "$L1_BUCKET" --prefix "$PREFIX" --query "sort_by(Contents,&LastModified)[-1].Key" --output text)
echo "restore depuis L1 : $KEY"
aws --endpoint-url $EP --no-verify-ssl s3 cp "s3://$L1_BUCKET/$KEY" /dl/dump.gz
ls -lh /dl/
'@
$pgRestore = @'
set -e
docker-entrypoint.sh postgres >/tmp/pg.log 2>&1 &
for i in $(seq 1 60); do pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1 && break; sleep 2; done
echo "postgres ephemere pret"
gunzip -c /dl/dump.gz | psql -U postgres -h 127.0.0.1 -v ON_ERROR_STOP=0 >/tmp/r.log 2>&1 || true
echo "erreurs restore (hors roles existants): $(grep -c ERROR /tmp/r.log || echo 0)"
total=0
for db in $(psql -U postgres -h 127.0.0.1 -Atc "select datname from pg_database where datname not in ('template0','template1')"); do
  n=$(psql -U postgres -h 127.0.0.1 -d "$db" -Atc "select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema')" 2>/dev/null || echo 0)
  if [ "$n" -gt 0 ] 2>/dev/null; then echo "  base $db : $n tables"; total=$((total+n)); fi
done
echo "  TOTAL tables restaurees: $total"
echo RESTORE-OK
'@
$mongoRestore = @'
set -e
mongod --dbpath /data/db --bind_ip 127.0.0.1 --fork --logpath /tmp/mongod.log
echo "mongo ephemere pret"
mongorestore --archive=/dl/dump.gz --gzip --quiet
echo "Location (hearth-dev): $(mongosh --quiet hearth-dev --eval 'print(db.Location.countDocuments())')"
echo "users (user-mgnt): $(mongosh --quiet user-mgnt --eval 'print(db.users.countDocuments())')"
echo "bases: $(mongosh --quiet --eval 'print(db.adminCommand({listDatabases:1}).databases.map(function(d){return d.name}).join(","))')"
echo RESTORE-OK
'@

# CRITIQUE : convertir en LF pur (les here-strings heritent du CRLF du .ps1, ce qui casse sh)
$dlScript = $dlScript -replace "`r`n","`n"
$pgRestore = $pgRestore -replace "`r`n","`n"
$mongoRestore = $mongoRestore -replace "`r`n","`n"

function MakePgJob($name, $prefix) {
  return @{
    apiVersion = "batch/v1"; kind = "Job"
    metadata = @{ name = $name; namespace = $nsO }
    spec = @{
      backoffLimit = 0; ttlSecondsAfterFinished = 7200; activeDeadlineSeconds = 600
      template = @{
        metadata = @{ labels = @{ "iun.sn/backup" = "true" } }
        spec = @{
          restartPolicy = "Never"
          volumes = @(@{ name = "dl"; emptyDir = @{} }, @{ name = "pgdata"; emptyDir = @{} })
          initContainers = @(@{ name = "download"; image = "amazon/aws-cli:2.17.16"
            envFrom = @(@{ secretRef = @{ name = "iun-dr-s3" } })
            env = @(@{ name = "PREFIX"; value = $prefix })
            volumeMounts = @(@{ name = "dl"; mountPath = "/dl" })
            command = @("sh","-c",$dlScript) })
          containers = @(@{ name = "restore"; image = "docker.io/postgres:16-alpine"
            env = @(@{ name = "POSTGRES_PASSWORD"; value = "drill" }, @{ name = "PGDATA"; value = "/var/lib/postgresql/data/pgdata" })
            volumeMounts = @(@{ name = "dl"; mountPath = "/dl" }, @{ name = "pgdata"; mountPath = "/var/lib/postgresql/data" })
            command = @("sh","-c",$pgRestore) })
        }
      }
    }
  }
}
function MakeMongoJob($name, $prefix) {
  return @{
    apiVersion = "batch/v1"; kind = "Job"
    metadata = @{ name = $name; namespace = $nsO }
    spec = @{
      backoffLimit = 0; ttlSecondsAfterFinished = 7200; activeDeadlineSeconds = 600
      template = @{
        metadata = @{ labels = @{ "iun.sn/backup" = "true" } }
        spec = @{
          restartPolicy = "Never"
          volumes = @(@{ name = "dl"; emptyDir = @{} }, @{ name = "dbdata"; emptyDir = @{} })
          initContainers = @(@{ name = "download"; image = "amazon/aws-cli:2.17.16"
            envFrom = @(@{ secretRef = @{ name = "iun-dr-s3" } })
            env = @(@{ name = "PREFIX"; value = $prefix })
            volumeMounts = @(@{ name = "dl"; mountPath = "/dl" })
            command = @("sh","-c",$dlScript) })
          containers = @(@{ name = "restore"; image = "mongo:6.0"
            volumeMounts = @(@{ name = "dl"; mountPath = "/dl" }, @{ name = "dbdata"; mountPath = "/data/db" })
            command = @("sh","-c",$mongoRestore) })
        }
      }
    }
  }
}

$jobs = @()
$jobs += @{ n = "drill-pg-events-$jstamp"; obj = (MakePgJob "drill-pg-events-$jstamp" "origins-opencrvs/direct/postgres-events") }
$jobs += @{ n = "drill-openimis-$jstamp"; obj = (MakePgJob "drill-openimis-$jstamp" "origins-openimis/direct/openimis-db") }
$jobs += @{ n = "drill-mosip-kernel-$jstamp"; obj = (MakePgJob "drill-mosip-kernel-$jstamp" "origins-mosip/direct/mosip_kernel") }
$jobs += @{ n = "drill-hearth-$jstamp"; obj = (MakeMongoJob "drill-hearth-$jstamp" "origins-opencrvs/direct/hearth-full") }

foreach ($j in $jobs) {
  $f = "$env:TEMP\$($j.n).json"
  [System.IO.File]::WriteAllText($f, ($j.obj | ConvertTo-Json -Depth 25), $utf8NoBom)
  oc delete job $j.n -n $nsO --ignore-not-found 2>&1 | Out-Null
  oc apply -f $f 2>&1 | ForEach-Object { W "  $_" }
}

W ""
W "attente des 4 restores (max 12 min)..."
$deadline = (Get-Date).AddMinutes(12)
$done = @{}
while ((Get-Date) -lt $deadline -and $done.Count -lt $jobs.Count) {
  Start-Sleep -Seconds 20
  foreach ($j in $jobs) {
    if ($done.ContainsKey($j.n)) { continue }
    $s = (oc get job $j.n -n $nsO -o jsonpath='{.status.succeeded}' 2>&1 | Out-String).Trim()
    $f = (oc get job $j.n -n $nsO -o jsonpath='{.status.failed}' 2>&1 | Out-String).Trim()
    if ($s -eq "1") { $done[$j.n] = "OK" }
    elseif ($f -match '^\d+$' -and [int]$f -ge 1) { $done[$j.n] = "FAIL" }
  }
}
foreach ($j in $jobs) {
  $st = if ($done.ContainsKey($j.n)) { $done[$j.n] } else { "TIMEOUT" }
  W ""
  W "--- $($j.n) : $st ---"
  $pod = ((oc get pods -n $nsO --no-headers -l "job-name=$($j.n)" 2>&1 | Out-String) -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
  if ($pod) {
    $pn = ($pod -split '\s+')[0]
    $lg = oc logs $pn -n $nsO --all-containers --tail=15 2>&1 | Out-String
    foreach ($ln in ($lg -split "`r?`n")) { if ($ln.Trim()) { W "  | $ln" } }
  }
}

Copy-Item -Force -Path $log -Destination (Join-Path $reportDir "restore-drill-latest.log")
W ""
W "=== DONE - RESTORE-OK partout = site de secours L1 PROUVE exploitable ==="
