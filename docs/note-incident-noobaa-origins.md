# Incident stockage objet NooBaa — cluster Origins — RÉSOLU le 19/07

**De :** Alioune Mbaye (astreinte temporaire), programme IUN
**Statut : RÉSOLU** — 19/07/2026 15:16 UTC. Durée de l'incident : ~2,5 jours (17/07 02:07 → 19/07 15:16).
**Sévérité :** Haute (indisponibilité d'objets S3, service partagé — IUN + Velero/Kopia `srmt-prod` impactés)

## Résumé exécutif

Le backingstore par défaut de NooBaa (`noobaa-default-backing-store`, pv-pool) est resté en phase `Rejected` / mode `ALL_NODES_OFFLINE` pendant ~2,5 jours. Conséquences : échec de toutes les écritures S3 (`NOT_ENOUGH_SPACE` trompeur — le volume était à 29 %) et indisponibilité en lecture des objets récents. **Aucune donnée n'a été perdue** : les objets redevenus illisibles ont été re-vérifiés lisibles après remédiation.

## Chronologie et cause racine

1. **17/07 02:07 UTC** — le pod agent pv-pool (`noobaa-default-backing-store-noobaa-pod-456e080d`) est **OOMKilled** (exit 137, 3e restart). Sa RSS plafonnait à ~400 Mo, la limite par défaut.
2. Après redémarrage, l'agent n'a **jamais réussi à ré-établir ses heartbeats** vers `wss://noobaa-mgmt` (`RPC CONNECT TIMEOUT` en boucle, WebSocket coincé côté serveur) — alors même que le pod était `Running 1/1`.
3. Le core NooBaa a déclaré le seul nœud de stockage offline → `ALL_NODES_OFFLINE` → backingstore `Rejected`, bucketclass `Rejected` → `allocate_node: no nodes for allocation` sur toutes les opérations. 17 454 events `BackingStorePhaseRejected` émis en 2j20h **sans alerte** — trou de supervision à combler.
4. Impact constaté : uploads bloqués (CronJobs de backup IUN suspendus 13 h), GET « Connection was closed » sur les objets récents, échecs Velero/Kopia (`velero-backups-*/srmt-prod`).

## Remédiation appliquée (19/07)

- **15:12** — suppression du pod agent pv-pool (recréation par l'operator) → reconnexion WebSocket propre.
- **15:16** — backingstore `Ready` / mode `OPTIMAL`. Test canary : PUT + GET + comparaison bit-à-bit OK. **Tous les objets précédemment illisibles (18-19/07) re-vérifiés LISIBLES** — l'indisponibilité n'était pas une corruption.
- **Prévention** — limite mémoire de l'agent pv-pool relevée (requests 600Mi / limit 1Gi, patch du backingstore) : l'OOM à 400 Mo est le déclencheur racine.

## Recommandations pour l'équipe plateforme

1. **Supervision** : alerte sur `backingstore.status.phase != Ready` et sur le mode `*_OFFLINE` (l'incident est resté invisible 2,5 jours malgré 17 000+ events).
2. **Dimensionnement** : valider la nouvelle limite mémoire de l'agent (1 Gi) et envisager `numVolumes: 2+` ou un second backingstore pour la redondance (un seul nœud pv-pool = SPOF intégral du S3).
3. **Vérifier Velero/Kopia** (`srmt-prod`) : relancer/valider les sauvegardes cluster échouées depuis le 17/07.
4. Bug possible côté NooBaa (agent incapable de se resynchroniser seul après OOM, WebSocket serveur coincé) : à signaler au support Red Hat ODF si récidive malgré la marge mémoire.

## Vérifications post-incident côté IUN

Canary écriture/lecture OK ; relecture de 100 % des objets des buckets IUN OK ; backup local de validation relancé ; les CronJobs quotidiens (locaux 01:00-02:30, miroir offsite L1 03:00, direct-to-L1 04:00) reprennent normalement. La réplication hors-site vers L1, mise en place pendant l'incident, reste en service — c'est elle qui a détecté le problème.
