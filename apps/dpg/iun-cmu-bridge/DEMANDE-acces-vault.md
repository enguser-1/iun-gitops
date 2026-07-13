# Demande plateforme — accès Vault pour le secret du bridge IUN CMU

**Demandeur :** Alex (Lead Dev IUN)
**Namespace :** `iun-opencrvs-dev`
**Service :** `iun-cmu-bridge` (Leg B de la chaîne EF-07 : affiliation CMU dans OpenIMIS)
**Date :** 2026-06-13

## Contexte

Le service `iun-cmu-bridge` doit lire son mot de passe OpenIMIS (clé `openimis-password`)
via **Vault Secrets Operator** (standard plateforme). La plomberie côté Kubernetes est déjà
en place et saine :

- `VaultAuth` : `iun-opencrvs-dev/iun-opencrvs-auth` — role `iun-opencrvs-role`, method
  kubernetes, ServiceAccount `default` — **HEALTHY / READY**.
- `VaultConnection` : `iun-platform/vault-heritage` →
  `https://vault-active.security.svc.cluster.local:8200` — **HEALTHY**.
- `VaultStaticSecret` déjà rédigé côté GitOps :
  `gitops/apps/dpg/iun-cmu-bridge/01-vaultstaticsecret.yaml` (type `kv-v2`,
  `vaultAuthRef: iun-opencrvs-auth`, `destination` = Secret `iun-cmu-bridge-creds`).

Il ne reste qu'à renseigner le **mount** et le **path** Vault (les deux seules valeurs
définies par la policy du role, donc côté Vault).

## Demande (3 points)

1. **Mount KV-v2 + chemin** que `iun-opencrvs-role` est autorisé à lire pour ce namespace.
   Convention observée ailleurs : `<namespace>/<app>` (ex. `iam/keycloak-db` sur le mount `cetud`).
   Proposition pour nous : `iun-opencrvs-dev/iun-cmu-bridge`.

2. **Écriture de la clé** `openimis-password` à ce chemin :
   ```
   vault kv put <mount>/<path> openimis-password='<valeur fournie séparément, hors de ce document>'
   ```
   — soit vous l'écrivez, soit vous m'accordez un droit d'écriture temporaire sur ce chemin.
   (La valeur courante n'est PAS incluse ici ; je vous la transmets par canal sécurisé.)

3. **Confirmation** que la policy attachée à `iun-opencrvs-role` couvre bien `read` (et `read`
   des métadonnées) sur ce chemin, pour que le VSO puisse synchroniser.

## Ce qui se passe ensuite (côté IUN, une fois 1–3 faits)

```
# renseigner mount + path dans 01-vaultstaticsecret.yaml, puis :
oc delete secret iun-cmu-bridge-creds -n iun-opencrvs-dev      # bascule sous gestion VSO (create:true)
oc apply -f gitops/apps/dpg/iun-cmu-bridge/01-vaultstaticsecret.yaml
oc get vaultstaticsecret iun-cmu-bridge-creds -n iun-opencrvs-dev   # attendu : SYNCED / HEALTHY
```

## Note

Aujourd'hui le Secret `iun-cmu-bridge-creds` existe déjà (créé manuellement, hors-git) et le
service fonctionne — **il n'y a pas d'urgence ni de régression**. Cette demande vise uniquement à
rendre le secret déclaratif via Vault (objectif GitOps / v1).
