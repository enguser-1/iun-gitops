# components/cnpg-cluster/base — Placeholder

À remplir phase 2 — `Cluster` PostgreSQL IUN via CloudNativePG.

CNPG Operator est déjà installé sur le cluster (autre équipe). On déploie ici uniquement la CR `Cluster` IUN, pas la Subscription.

## À définir avant remplissage

- Nombre d'instances (3 pour HA en prod, 1 en dev).
- StorageClass : préférer `ocs-storagecluster-ceph-rbd` (ODF déjà installé).
- Politique de backup : `ScheduledBackup` quotidien vers ODF NooBaa ou S3 externe.
- Schema / users à provisionner via `managed.roles`.
