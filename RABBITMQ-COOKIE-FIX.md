# RabbitMQ `.erlang.cookie` — diagnostic et fix

## Symptôme

```
[error] Cookie file /var/lib/rabbitmq/.erlang.cookie must be accessible by owner only
Kernel pid terminated (application_controller)
  ({application_start_failure,rabbitmq_prelaunch, ...})
```

Pod `openimis-rabbitmq-0` en CrashLoopBackOff après chaque restart, y compris après wipe complet du PVC.

## Root cause

Erlang/OTP refuse de démarrer si `.erlang.cookie` est lisible par le groupe ou les autres. La vérification est faite dans `auth.erl` (`init_no_setcookie/0`) : elle rejette tout fichier dont `mode band 8#077 ≠ 0`. Seuls `0600` et `0400` passent.

L'entrée `docker-entrypoint.sh` de `rabbitmq:3.13-management` ne force pas le mode du cookie. Quand Erlang crée le cookie lui-même au premier boot, il applique le umask hérité du process — `0022` sur cette image → fichier en `0644`. Refus immédiat.

Sur OpenShift on a en plus un deuxième problème de récupération : si le cookie a été écrit lors d'un run précédent par un UID différent (cas SCC restricted-v2 avec UID random), un simple `chmod` par l'UID `999` du run suivant échoue avec `EPERM`. Le wipe du PVC n'aide pas tant que l'image continue à recréer le cookie au même mode.

## Alternatives évaluées

| Option | Verdict |
|---|---|
| **A. Activer `fsGroup: 999`** | Déjà présent dans le manifest. `fsGroup` propage l'ownership (`chown :999`) mais **ne touche pas les bits de mode** — donc inopérant ici. Écarté. |
| **B. Secret monté en `defaultMode: 0400` sur `/var/lib/rabbitmq/.erlang.cookie` (subPath)** | Marche en théorie. En pratique : (1) le file est mounted `root:root` mode 0400, OK pour Erlang ; mais (2) certaines releases de l'image tentent d'écrire dans le cookie (read-only mount → EROFS), et (3) la variable d'env `RABBITMQ_ERLANG_COOKIE` est **dépréciée** depuis 3.9 (warning + comportement non garanti). Trop de friction. Écarté. |
| **C. initContainer `cookie-fix` UID 0 sur même PVC** | **Retenu**. Tourne avant le main container, supprime un cookie au mode invalide, regénère via `/dev/urandom` avec `umask 077`, force `chown 999:999` + `chmod 600`. Robuste face à toute corruption antérieure (mauvais mode ou mauvais owner). |

## Fix appliqué — Itération 1

Patch sur `apps/dpg/openimis/manifests/12-rabbitmq.yaml` :

- Ajout `initContainers: [cookie-fix]` avant le container `rabbitmq`.
- `securityContext` du initContainer : `runAsUser: 0`. Acceptable car la pod a déjà l'annotation `openshift.io/required-scc: anyuid` et le ServiceAccount `openimis-sa` y est bound.
- Le initContainer monte le même PVC `data` sur `/var/lib/rabbitmq` que le main container.
- Logique : inspection → suppression si mode != 600/400 → régénération si absent → chown+chmod final → `stat` pour log.

Pas de modif sur le main container, pas de modif sur le PVC ni la storageClass (`ocs-external-storagecluster-ceph-rbd` inchangée).

## Pourquoi pas `securityContext.fsGroup` tout seul

Confirmé : `fsGroup` ne pose **que** l'ownership group + un `chmod g+s` sur les directories. Il ne change pas les bits de mode des fichiers existants ni des fichiers créés par les apps après le mount. Le cookie aurait toujours été en `0644`.

## Itération 2 — admission SCC `anyuid` refuse `capabilities.add`

### Symptôme
```
pods "openimis-rabbitmq-0" is forbidden: unable to validate against any security context constraint:
  provider anyuid: .initContainers[0].capabilities.add: Invalid value: "CHOWN":       capability may not be added
  provider anyuid: .initContainers[0].capabilities.add: Invalid value: "DAC_OVERRIDE": capability may not be added
  provider anyuid: .initContainers[0].capabilities.add: Invalid value: "FOWNER":      capability may not be added
```

### Cause
La SCC `anyuid` d'OpenShift a `allowedCapabilities: []` (liste vide). Toute capability passée dans `capabilities.add` du PodSpec est rejetée à l'admission, même si elle fait partie des caps Linux par défaut. Pour autoriser des `add`, il faut une SCC plus permissive (`privileged`, `hostmount-anyuid`, ou une SCC custom).

### Fix retenu Itération 2 (appliqué)
Suppression du bloc `capabilities` complet (à la fois `drop` et `add`) du `initContainer.securityContext`. Le container tourne déjà en `runAsUser: 0` (autorisé par anyuid). Root avec les **caps Linux par défaut accordées par OCP** (typiquement `CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID, NET_BIND_SERVICE, SYS_CHROOT, KILL, AUDIT_WRITE, SETFCAP, SETPCAP, NET_RAW`) peut faire `chmod` et `chown` sur n'importe quel fichier du PVC sans cap supplémentaire — ces caps étaient ajoutées par habitude pour contrer un éventuel `drop: ALL`, mais on n'a aucun `drop: ALL` sur ce initContainer.

`securityContext` final du initContainer :
```yaml
securityContext:
  runAsUser: 0
  runAsNonRoot: false
  allowPrivilegeEscalation: false
```

Le main container (`rabbitmq`) conserve son `capabilities.drop: [ALL]` — il tourne en UID 999, n'a besoin d'aucune cap pour bind sur 5672 (port > 1024).

### Fix 3 (si fix 2 ne suffit pas — non appliqué, documenté)

Si on découvre un cas où root sans caps explicites n'arrive toujours pas à chmod le cookie (ex: SELinux MCS labels qui isolent les containers du même pod, ou fs ACL exotiques), passer à une SCC custom `iun-anyuid-caps` :

```yaml
# Ressource cluster-scoped : a appliquer HORS GitOps en cluster-admin
# (Argo iun-argocd est namespaced)
apiVersion: security.openshift.io/v1
kind: SecurityContextConstraints
metadata:
  name: iun-anyuid-caps
priority: 10
allowPrivilegedContainer: false
allowHostNetwork: false
allowHostPID: false
allowHostIPC: false
allowHostPorts: false
allowedCapabilities:
  - CHOWN
  - FOWNER
  - DAC_OVERRIDE
defaultAddCapabilities: []
requiredDropCapabilities: ["MKNOD"]
runAsUser:
  type: RunAsAny
seLinuxContext:
  type: MustRunAs
fsGroup:
  type: RunAsAny
supplementalGroups:
  type: RunAsAny
volumes: ["configMap","downwardAPI","emptyDir","persistentVolumeClaim","projected","secret"]
users: []
groups: []
```

Puis binding ciblé :
```bash
oc adm policy add-scc-to-user iun-anyuid-caps -z openimis-sa -n openimis-dev
```

Et dans le manifest, remplacer l'annotation :
```yaml
metadata:
  annotations:
    openshift.io/required-scc: iun-anyuid-caps   # remplace anyuid
```

Le fix 3 ajoute des privilèges restreints au SA `openimis-sa` (juste CHOWN/FOWNER/DAC_OVERRIDE, rien de plus). À éviter tant que fix 2 suffit.

## Procédure d'application

Exécuter depuis Windows PowerShell (UTF-8 BOM requis pour le script) :

```powershell
cd C:\IUN_APP\gitops
.\Fix-Rabbitmq-Cookie.ps1
```

Options utiles :
- `-WipePvc` : reset complet (queues + messages persistants effacés).
- `-SkipGitPush` : si le manifest est déjà en main et qu'on veut juste relancer le pod.
- `-SkipArgoRefresh` : utile en debug local.
- `-ForceLocalApply` : bypass Argo, `oc apply -f` direct (drift Argo à corriger après).

## Vérification de succès

Dans les logs (post-init) on doit voir :

1. Logs initContainer (via `oc logs openimis-rabbitmq-0 -c cookie-fix`) :
   ```
   [cookie-fix] etat initial de /var/lib/rabbitmq :
   ...
   [cookie-fix] resultat final :
     /var/lib/rabbitmq/.erlang.cookie -> mode=600 owner=rabbitmq(999):rabbitmq(999) size=48
   ```
2. Logs container `rabbitmq` :
   ```
   Starting RabbitMQ 3.13.x on Erlang ...
   node           : rabbit@openimis-rabbitmq-0
   Server startup complete; N plugins started.
   started TCP listener on [::]:5672
   ```
3. Plus aucune mention de `Cookie file ... must be accessible by owner only`.

Après ça les 3 workers Celery doivent passer de `0/1` à `1/1` en 30-60s (probe AMQP).

## Statut frontend

Le `oc patch ... remove /spec/template/spec/nodeSelector` enlève la contrainte. Vérification automatique dans le script (étape 6) :

```
oc get pods -n openimis-dev -l app.kubernetes.io/component=frontend -o wide
```

Si encore `Pending` après force-delete, le script extrait automatiquement la section `Events:` du `describe pod` — chercher en priorité :
- `0/N nodes are available` → autre contrainte (taints, affinité, PVC mode).
- `FailedScheduling` + `pod has unbound immediate PersistentVolumeClaims` → vérifier la PVC du frontend.
- `Insufficient cpu/memory` → ressources cluster saturées.

## Suivi

- [x] Itération 1 (initContainer cookie-fix) : appliqué.
- [x] Itération 2 (suppression `capabilities.add`) : appliqué.
- [ ] Vérifier après reboot du pod que le cookie est bien en `mode=600 owner=999:999`.
- [ ] Si fix 2 KO : Fix 3 documenté ci-dessus (SCC custom `iun-anyuid-caps`). Cluster-admin requis, hors GitOps.
- [ ] Si OK : envisager d'upstream un commentaire dans `12-rabbitmq.yaml` pointant vers la doc RabbitMQ clustering.
- [ ] À moyen terme : remplacer `rabbitmq:3.13-management` par un build interne qui force `umask 077` dans son entrypoint — évite le initContainer.
- [ ] Vérifier que le secret `openimis-rabbitmq` (default user / password AMQP) n'a pas besoin d'être rotaté après un wipe.
