# iun-cmu-bridge — gestion du secret OpenIMIS (Vault Secrets Operator)

Le bridge lit le mot de passe OpenIMIS depuis le Secret Kubernetes `iun-cmu-bridge-creds`
(clé `openimis-password`), référencé par `02-deployment.yaml` via `secretKeyRef`.

**Standard plateforme IUN = HashiCorp Vault + Vault Secrets Operator (VSO).** (Le cluster Heritage
n'a PAS sealed-secrets ; il a VSO v1.4.0 et ESO v0.11.0 — IUN utilise VSO.) Le secret vit dans
**Vault** ; on committe seulement un `VaultStaticSecret` (`01-vaultstaticsecret.yaml`) qui est un
**pointeur** — aucune donnée sensible dans git, même pas chiffrée. Le VSO lit Vault et crée le
Secret en mémoire cluster :

```
Vault (kv-v2)  --[VSO + VaultAuth iun-opencrvs-auth]-->  Secret iun-cmu-bridge-creds  -->  Deployment
```

Plomberie déjà en place dans `iun-opencrvs-dev` (rien à créer) :
- `VaultAuth` **iun-opencrvs-auth** — role `iun-opencrvs-role`, auth kubernetes (SA `default`), HEALTHY.
- `VaultConnection` **iun-platform/vault-heritage** → `https://vault-active.security.svc.cluster.local:8200`, HEALTHY.

## Étapes

### 1. Confirmer le moteur KV + le chemin (les 2 seuls inconnus, côté Vault)

Ils dépendent de la policy de `iun-opencrvs-role`. Avec un token Vault autorisé :

```powershell
# lister les moteurs KV (trouver le mount, ex. 'heritage' ou 'iun' ou 'kv')
vault secrets list
# convention de chemin observée ailleurs : <namespace-ish>/<app>  (ex. iam/keycloak-db)
```

Reporter ces 2 valeurs dans `01-vaultstaticsecret.yaml` (champs `mount:` et `path:`, marqués TODO).
Si tu n'as pas de token Vault : demander à l'équipe plateforme le **mount KV-v2** et le **chemin**
autorisés pour `iun-opencrvs-role`, ainsi que le droit d'y écrire.

### 2. Écrire le mot de passe dans Vault (une fois)

```powershell
vault kv put <MOUNT>/<PATH> openimis-password='IunCmuBridge2026!'
# ex. : vault kv put heritage/iun-opencrvs-dev/iun-cmu-bridge openimis-password='...'
```

### 3. Basculer le Secret sous gestion VSO

Le Secret `iun-cmu-bridge-creds` a été créé manuellement (Sprint 3). Comme le VSS utilise
`destination.create: true` (le VSO veut posséder le Secret), supprimer d'abord le manuel :

```powershell
$flag = '--insecure-skip-tls-verify=true'
oc delete secret iun-cmu-bridge-creds -n iun-opencrvs-dev $flag    # le VSO va le recréer
oc apply -f 01-vaultstaticsecret.yaml $flag
# vérifs :
oc get vaultstaticsecret iun-cmu-bridge-creds -n iun-opencrvs-dev $flag   # SYNCED/HEALTHY True
oc get secret iun-cmu-bridge-creds -n iun-opencrvs-dev $flag              # recréé par le VSO
oc rollout restart deployment/iun-cmu-bridge -n iun-opencrvs-dev $flag
```

Puis ajouter le VSS aux ressources Kustomize (Argo le synchronisera) :

```yaml
# kustomization.yaml -> resources:
  - 01-vaultstaticsecret.yaml
```

## Ce qui est committable / ce qui ne l'est jamais

À COMMITTER (sans danger) : `01-vaultstaticsecret.yaml` (pointeur, zéro secret), `02-deployment.yaml`,
ce README.
À NE JAMAIS COMMITTER : le mot de passe en clair, un `Secret` avec `data`/`stringData` réels.
Filet `.gitignore` : `*-secret.yaml` sauf `*.example.yaml`.

OBSOLÈTES (peuvent être supprimés) : `Seal-CmuSecret.ps1`, `00-secret.example.yaml`
(approche sealed-secrets, non retenue). `del` côté Windows.

## Rotation

1. `vault kv put <MOUNT>/<PATH> openimis-password='<nouveau>'` (+ changer côté OpenIMIS).
2. Le VSO resync sous `refreshAfter` (1h) ; pour appliquer tout de suite :
   `oc rollout restart deployment/iun-cmu-bridge -n iun-opencrvs-dev`.

Aucune sauvegarde de clé à gérer (contrairement à sealed-secrets) : la source de vérité est Vault.
