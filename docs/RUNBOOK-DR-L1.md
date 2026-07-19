# Runbook de reprise après sinistre (DR) — Programme IUN → cluster L1

**Version :** 1.0 — 19 juillet 2026
**Auteur :** Alioune Mbaye (Lead Dev IUN)
**Portée :** reconstruction du programme IUN (état civil OpenCRVS, openIMIS, MOSIP) sur le cluster de secours `origins.l1.heritage.africa` en cas de perte du cluster de production `origins.heritage.africa`.

---

## 1. Objectifs de reprise

| Indicateur | Cible actuelle | Commentaire |
|---|---|---|
| **RPO** (perte de données max.) | 24 h | Backups directs quotidiens 04:00 vers L1 ; réduire à 6-12 h en augmentant la fréquence si besoin. |
| **RTO** (délai de reprise) | Heures → jours | **Bloqué par l'architecture** : L1 est un cluster IBM Z (s390x), les images IUN sont amd64. La reprise applicative exige d'abord des workers x86 sur L1 (demande ops en cours) OU un cluster x86 alternatif. |
| **Couverture données** | 100 % vérifiée | Toutes les bases des 3 namespaces + secrets + code, répliquées et **restauration testée depuis L1** le 19/07. |

**État au 19/07/2026 :** le DR *données + code + secrets* est opérationnel et prouvé. Le DR *applicatif* (redémarrage du service) attend les nœuds x86.

---

## 2. Ce qui est répliqué sur L1 (bucket OBC `iun-dr-backups`, ns `iun-dr`)

Endpoint S3 : `https://s3-openshift-storage.apps.origins.l1.heritage.africa` (TLS interne → `--no-verify-ssl`).

| Objet L1 | Contenu | Source |
|---|---|---|
| `origins-opencrvs/direct/hearth-full-*.archive.gz` | Toutes les bases MongoDB (hearth-dev, user-mgnt, application-config, civil_registry_iun, notification, webhooks…) | CronJob `backup-direct-l1-hearth` 04:00 |
| `origins-opencrvs/direct/postgres-events-*.sql.gz` | Base `events` (service events v2) | CronJob `backup-direct-l1-pg-events` 04:00 |
| `origins-openimis/direct/openimis-db-*.sql.gz` | Base openIMIS (279 tables) | CronJob `backup-direct-l1-openimis-db` 04:00 |
| `origins-mosip/direct/mosip_kernel-*.sql.gz` | **Pool d'UIN** (séquence `iun_uin_issued_id_seq`) — donnée MOSIP critique | CronJob `backup-direct-l1-mosip` 04:00 |
| `origins-mosip/direct/mosip_master-*.sql.gz` | Données de référence MOSIP (75 tables) | idem |
| `origins-mosip/direct/mosip_keymgr-*.sql.gz` | Métadonnées de clés MOSIP | idem |
| `origins-mosip/softhsm-kernel.tgz` | Volume softhsm (clés) | export ponctuel |
| `secrets/iun-secrets.enc` | **38 secrets applicatifs**, chiffrés AES-256 (openssl, PBKDF2 200k) | export ponctuel — passphrase détenue par le Lead Dev |
| `gitops/gitops-latest.bundle` | Dépôt gitops complet (tous les manifestes, toutes branches) | rafraîchi à chaque sync |

⚠️ Il existe aussi un miroir `backup-offsite-l1` (03:00) qui recopie le bucket local d'Origins vers L1 sous `origins-opencrvs/` et `origins-openimis/` (hors `direct/`). Les copies `direct/` sont plus fiables (elles contournent tout problème du S3 local d'Origins).

---

## 3. Prérequis avant reprise applicative sur L1

1. **Nœuds workers x86_64 sur L1** (voir `note-demande-workers-x86-l1.md`) OU cluster x86 alternatif.
2. Namespaces créés : `iun-opencrvs-dev`, `iun-openimis-dev`, `iun-mosip-dev`.
3. Storage class : L1 utilise `ocs-storagecluster-ceph-rbd` (Origins : `ocs-external-storagecluster-ceph-rbd`). **Tous les PVC/manifestes doivent être re-mappés** sur la classe L1.
4. Opérateurs : GitOps (présent sur L1), CNPG (pour MOSIP), ODF (présent). VSO/Vault : **absent de L1** — les secrets seront restaurés depuis l'archive chiffrée, pas depuis Vault.
5. Accès registry : les images IUN doivent être disponibles pour L1 (mirror registry ou re-build).

---

## 4. Procédure de reprise (étapes)

### Étape 0 — Accès
```
oc login --token=... --server=https://api.origins.l1.heritage.africa:6443
```

### Étape 1 — Récupérer le gitops depuis L1
Depuis un pod ou poste avec accès au bucket L1 :
```
aws --endpoint-url $L1_S3_ENDPOINT --no-verify-ssl s3 cp \
  s3://$L1_BUCKET/gitops/gitops-latest.bundle ./gitops.bundle
git clone gitops.bundle gitops
```

### Étape 2 — Créer les namespaces + restaurer les secrets
```
oc new-project iun-opencrvs-dev ; oc new-project iun-openimis-dev ; oc new-project iun-mosip-dev
# Récupérer et déchiffrer l'archive de secrets (passphrase demandée) :
aws --endpoint-url $L1_S3_ENDPOINT --no-verify-ssl s3 cp \
  s3://$L1_BUCKET/secrets/iun-secrets.enc ./iun-secrets.enc
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in iun-secrets.enc -pass pass:'<PASSPHRASE>' \
  | oc apply -f -
```
(Chaque secret porte son namespace d'origine dans le YAML ; `oc apply -f -` les recrée dans les 3 namespaces.)

### Étape 3 — Déployer le socle depuis le gitops (adapter storage class)
Remplacer `ocs-external-storagecluster-ceph-rbd` → `ocs-storagecluster-ceph-rbd` dans les manifestes, puis appliquer via Argo CD ou `oc apply`. Déployer d'abord les bases (mongo, postgres-events, openimis-db, mosip CNPG), attendre qu'elles soient prêtes.

### Étape 4 — Restaurer les données (depuis L1)
Le pattern éprouvé (script `restore-drill-l1.ps1`) : un Job par base, initContainer aws-cli qui télécharge le dernier dump depuis L1, conteneur moteur qui restaure.

- **MongoDB (hearth)** : `mongorestore --archive=<hearth-full-*.archive.gz> --gzip` dans le pod mongo cible (restaure les 9 bases dont user-mgnt et hearth-dev).
- **postgres-events** : `gunzip -c <postgres-events-*.sql.gz> | psql -U app -d events`.
- **openimis-db** : `gunzip -c <openimis-db-*.sql.gz> | psql` (pg_dumpall — recrée la base).
- **MOSIP** : restaurer `mosip_kernel` (user `app`), `mosip_master` (user `masteruser`), `mosip_keymgr` (user `keymgruser`) — mots de passe depuis les secrets restaurés (`mosip-postgres-app`, `db-common-secrets`). Restaurer le volume softhsm depuis `softhsm-kernel.tgz`.

⚠️ **Ordre MOSIP** : restaurer AVANT de démarrer `iun-uin-service`/`idgenerator`, sinon risque de ré-émission d'UIN déjà attribués (la séquence `iun_uin_issued_id_seq` de `mosip_kernel` porte le compteur).

### Étape 5 — Elasticsearch / OpenSearch
Non répliqués (index reconstructibles). Après restauration des bases, laisser le service events/search réindexer. Sur OpenCRVS, une réindexation complète peut être nécessaire (`search` service).

### Étape 6 — Bascule DNS
Repointer les routes publiques (`*.apps.origins.heritage.africa` → `*.apps.origins.l1.heritage.africa`) une fois la stack L1 validée. Prévenir MOSIP/partenaires du changement d'endpoint.

### Étape 7 — Vérification E2E
Déclaration de naissance test → BRN régional + VID → impression du certificat. Confirmer que le compteur d'UIN reprend au-delà du dernier UIN émis (pas de collision).

---

## 5. Test de restauration (à rejouer périodiquement)

Le script `restore-drill-l1.ps1` restaure les 4 bases DEPUIS L1 dans des instances éphémères (aucun impact production) et vérifie les comptes. **Dernier passage réussi : 19/07/2026** — events 9 tables, openimis 279 tables, mosip_kernel 7 tables, hearth 167 Location. À rejouer mensuellement et après tout changement majeur.

---

## 6. Limites connues / dette

- **RTO applicatif non garanti** tant que L1 est 100 % s390x (bloquant #1).
- Elasticsearch/InfluxDB/Redis non répliqués (volontaire — reconstructibles).
- Un seul jeu de credentials L1 (secret `iun-dr-s3`) ; la passphrase de l'archive de secrets est détenue hors-ligne par le Lead Dev — **sans elle, les secrets sont irrécupérables**.
- Le miroir `offsite-l1` (03:00) peut copier des objets d'un S3 Origins dégradé ; toujours préférer les copies `direct/` (04:00) pour un restore.
- Gitops sans remote git avant ce chantier ; désormais le bundle sur L1 tient lieu de copie distante (un vrai Gitea L1 est prévu dès les workers x86).

---

## 7. Contacts

- Lead Dev IUN : alioune.mbaye@pm.me (détenteur de la passphrase de secrets)
- Ops plateforme Heritage : pour workers x86 L1 et incidents ODF/NooBaa
