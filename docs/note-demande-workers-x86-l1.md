# Demande d'ajout de nœuds workers x86_64 au cluster L1 — Programme IUN Sénégal

**De :** Alioune Mbaye, Lead Senior Developer, programme IUN
**À :** Équipe opérations plateforme Heritage
**Date :** 19 juillet 2026
**Objet :** Ajout de nœuds compute x86_64 au cluster `origins.l1.heritage.africa` pour la haute disponibilité du programme IUN

## Contexte

Le programme IUN (identité unique nationale — état civil OpenCRVS, openIMIS, MOSIP) tourne aujourd'hui exclusivement sur le cluster Origins (`origins.heritage.africa`, namespaces `iun-opencrvs-dev`, `iun-openimis-dev`, `iun-mosip-dev`). Un incident de perte de données le 16/07 (base postgres-events, récupérée depuis) a confirmé l'urgence d'une stratégie de haute disponibilité multi-cluster, objectif inscrit au readiness du programme.

Le cluster L1 (`origins.l1.heritage.africa`, OpenShift 4.19.7) a été retenu comme site de réplique. Nos vérifications du 19/07 montrent toutefois que **L1 est homogène IBM Z (s390x)** : les 3 workers (`compute01-03`) et les 3 control-plane sont en architecture s390x.

## Problème

Les images de la pile IUN ne sont publiées qu'en **amd64** (et partiellement arm64) : OpenCRVS 1.9.x (ghcr.io/opencrvs), MOSIP, openIMIS, MongoDB, Elasticsearch, MinIO, Gitea. Aucune ne peut s'exécuter sur les nœuds s390x actuels de L1 (`no image found in image index for architecture "s390x"` constaté). La recompilation de l'ensemble de la pile pour s390x n'est pas une option réaliste : plusieurs briques (MongoDB community, Elasticsearch, MinIO) n'offrent pas de support s390x officiel.

## Mesure conservatoire déjà en place

En attendant, nous utilisons le **stockage S3 NooBaa/ODF de L1** (natif s390x, opérationnel) comme cible hors-site : réplication quotidienne des backups (Hearth/MongoDB, postgres-events, openimis-db, MinIO) et du dépôt gitops complet (bundle git) depuis Origins. Le RPO données est donc couvert ; le RTO applicatif reste dépendant d'Origins.

## Demande

1. **Ajout de nœuds workers x86_64 au cluster L1** (OpenShift 4.19 supporte les clusters de calcul multi-architecture — payload multi-arch et MachineSet x86 dédiés) :
   - dimensionnement cible pour la réplique IUN complète : **3 workers x86_64, 16 vCPU / 64 Go RAM / 200 Go disque chacun** (aligné sur l'empreinte actuelle des 3 namespaces sur Origins, marge comprise) ;
   - un dimensionnement de démarrage à 2 workers 8 vCPU / 32 Go est acceptable pour la première phase (stack au repos, sans charge).
2. **Création des namespaces** `iun-opencrvs-dev`, `iun-openimis-dev`, `iun-mosip-dev`, `iun-gitea` sur L1 avec les mêmes quotas qu'Origins, et droits d'administration de namespace pour le compte `alioune@accel-tech.net` (déjà provisionné sur L1).
3. Confirmation que l'**egress réseau entre les deux clusters** (Origins ⇄ L1, HTTPS 443 + S3) restera autorisé au niveau infrastructure.

## Impact si non réalisé

Sans workers x86 sur L1 (ou cluster x86 alternatif), le programme IUN reste mono-site pour l'exécution applicative : indisponibilité complète du service d'état civil en cas de perte d'Origins, seule la restauration différée des données étant garantie.

Nous restons disponibles pour affiner le dimensionnement ou tester un premier worker pilote.
