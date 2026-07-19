# Incident stockage objet NooBaa — cluster Origins — corruption silencieuse d'objets

**De :** Alioune Mbaye, Lead Senior Developer, programme IUN
**À :** Équipe opérations plateforme Heritage
**Date :** 19 juillet 2026
**Sévérité proposée :** Haute (intégrité de données, service partagé)

## Symptômes constatés (namespaces iun-opencrvs-dev / iun-openimis-dev)

1. **Objets S3 illisibles après coup** : des objets uploadés avec succès dans des buckets OBC NooBaa (`s3.openshift-storage.svc`) deviennent illisibles quelques heures plus tard. GET → « Connection was closed before we received a valid response ». Constaté sur tous les objets écrits les 18 et 19/07 ; les objets du 17/07 restent lisibles. Exemple reproductible : `s3://iun-backups-bc6241ae-.../hearth/hearth-full-20260718-010001.archive.gz` (l'objet apparaît dans les listings avec sa taille normale, seul le GET échoue).
2. **Écritures dégradées** : nos CronJobs de backup de la nuit du 19/07 (01:00-02:00) sont restés bloqués plus de 13 h en cours d'upload (purgés depuis).
3. Un objet vérifié lisible le 17/07 à 21:49 (dump de 27 Mo) est devenu illisible depuis — la corruption est postérieure à l'écriture.

## Diagnostic côté IUN (19/07 14:36)

```
oc get backingstore -n openshift-storage
NAME                           TYPE      PHASE      AGE
noobaa-default-backing-store   pv-pool   Rejected   2y101d
```

- Le backingstore par défaut (pv-pool) est en phase **Rejected**.
- Le pod `noobaa-default-backing-store-noobaa-pod-456e080d` a redémarré 3 fois, dernier redémarrage il y a ~2,5 jours — fenêtre qui coïncide avec le début des corruptions.
- `noobaa` (mcg-core) affiche Ready ; noobaa-core/db/endpoint Running.

Notre hypothèse : les chunks des objets récents résident sur le pv-pool rejeté/dégradé ; le endpoint NooBaa échoue à les servir. L'impact dépasse IUN : tout consommateur d'OBC NooBaa sur Origins est exposé (le registry interne s'appuie également sur cette couche via RGW).

## Mesures conservatoires prises côté IUN

- Réplication hors-site quotidienne des backups et du dépôt gitops vers le S3 du cluster L1 (opérationnelle depuis le 19/07).
- Dumps frais (Hearth/MongoDB, postgres-events, openimis-db) poussés directement vers L1 en contournant le S3 Origins.

## Demandes

1. Investigation et remise en état du backingstore `noobaa-default-backing-store` (pv-pool Rejected) ; vérification de l'intégrité du PV sous-jacent.
2. Analyse des objets écrits depuis le ~17/07 sur les buckets OBC Origins (rebuild/scrub NooBaa) et avis sur leur récupérabilité.
3. Retour d'information sur la cause (saturation du pv-pool ? PV endommagé ? éviction ?) pour notre dossier d'incident readiness.

Contact : alioune.mbaye@pm.me — disponibles pour reproduire le symptôme à la demande.
