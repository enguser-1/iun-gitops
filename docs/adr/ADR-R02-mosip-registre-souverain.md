# ADR-R02 — MOSIP, générateur central et souverain d'identité

- **Statut** : Accepted (tranché par le Lead Senior Developer le 2026-08-20)
- **Date** : 2026-08-20
- **Décideurs** : Lead Senior Developer (Alex), Architecte plateforme
- **À porter en comité** : ANIU, MINT, CDP — pour ratification et pour l'arbitrage A1 qui en découle
- **Format** : MADR 3.0
- **Programme** : Identifiant Unique National (IUN) — République du Sénégal
- **Remplace / complète** : ADR-R01 (longueur d'UIN = 10) — *référencé par `apps/dpg/iun-uin-service/README.md` mais absent du dépôt, à reconstituer*
- **Sources** :
  - Relevé du code `iun-uin-bridge` v3.6 et de `iun-uin-service`, 2026-08-20
  - Dossier *Plateforme d'interopérabilité ANIU* v1.2, §11 — `architecture/hub-aniu/`
  - Rapports `v40-deploy_20260820_111647.log` et `v40-verif_20260820_112815.log`

---

## 1. Contexte

### 1.1 Ce qui a déclenché la décision

L'audit du câblage d'identité mené le 2026-08-20 a établi un fait qui n'était consigné
nulle part : **il n'y avait pas de MOSIP sur le chemin d'identité.**

Le service appelé par le bridge, `iun-uin-service`, est un microservice Python écrit le
2026-06-03 en remplacement *drop-in* de `kernel-idgenerator-service`, à la suite du pivot
décidé après les défauts rencontrés sur les *charts* MOSIP amont. Il expose l'API de MOSIP
et persiste dans la base `mosip_kernel`, mais il n'est pas MOSIP. Ni **ID Repository**, ni
**ABIS**, ni **IDA** n'étaient déployés.

La surface d'appel réelle se résumait à un seul point d'entrée :

```
POST {UIN_SERVICE_URL}/v1/idgenerator/uin   -> un nombre de 10 chiffres
```

Tout le reste — VID, correspondance UIN ↔ VID, statut de l'identifiant — était produit par
le bridge et stocké dans le MongoDB de Hearth, la base d'OpenCRVS.

### 1.2 Pourquoi c'était bloquant

Trois conséquences, dont une rédhibitoire pour le lot L1 :

1. **Un décès créait une identité neuve.** Le défunt n'ayant pas d'UIN — l'attribution ne
   se faisant qu'à la naissance —, le bridge en frappait un nouveau, avec un VID nouveau.
   L'événement `civil.death.registered` partait donc avec un identifiant que *personne ne
   connaissait* : IPRES ne retrouvait pas son pensionné, la DGE son électeur. **La cascade
   décès ne pouvait pas fonctionner sur ce câblage.**
2. **Le VID n'était pas un VID.** Généré localement au bon format, mais jamais émis par un
   registre : le révoquer n'avait aucun effet observable, alors que toute l'architecture
   du hub repose sur « le VID est l'identifiant d'échange, révocable ».
3. **L'UIN était un numéro sans identité derrière.** D'où, mécaniquement, ni dédoublonnage,
   ni authentification, ni preuve vérifiable.

### 1.3 La question posée

> MOSIP est-il le **registre d'identité** du Sénégal, ou un **générateur de numéros** pour
> l'état civil ?

Les deux réponses étaient défendables. Ce qui ne l'était pas, c'était de laisser la question
ouverte pendant qu'on raccorde treize entités.

---

## 2. Décision

> **MOSIP est le générateur central et souverain d'identité du Sénégal.**
> Le registre ne distribue pas seulement des numéros : il détient l'identité derrière le
> numéro et porte son cycle de vie.

**Périmètre retenu** : **ID Repository d'abord**, avec le jeu démographique minimal issu de
l'état civil et sans biométrie. **ABIS** (dédoublonnage) et **IDA** (authentification,
vérification sans divulgation) sont reportés au lot **L2** — report décidé et écrit, non
oublié.

### 2.1 Le corollaire d'implémentation

La décision se heurtait immédiatement à un fait : *il n'existait aucun ID Repository à
appeler*. « Écrire l'IdRepo dès L1 » n'était donc pas une modification du bridge, mais un
composant à créer. Le parti retenu évite d'attendre un déploiement MOSIP amont qui a déjà
échoué une fois :

> **Les chemins MOSIP sont écrits dans le service souverain lui-même.** Le bridge ne parle
> jamais qu'au contrat MOSIP — `/idrepository/v1/identity`, `/v1/vidgenerator/vid`, statut
> d'UIN. Le jour où le kernel amont fonctionne, c'est un **repointage d'URL**, pas une
> réécriture.

Une seule variable d'environnement porte ce basculement : `IDENTITY_SERVICE_URL`.

---

## 3. Options envisagées

### Option A — Déployer la pile MOSIP amont complète, puis câbler

- **Pour** : conformité littérale au produit, dédoublonnage et authentification acquis d'emblée.
- **Contre** : dépend d'un déploiement qui a échoué en juin après un effort documenté ;
  exige une chaîne biométrique qui n'existe pas sur Origins ; repousse la cascade décès
  d'un ou plusieurs lots.
- **Rejetée** : le calendrier du lot L1 ne survit pas à cette dépendance.

### Option B — Assumer un simple générateur de numéros

- **Pour** : rien à faire, effort nul.
- **Contre** : le hub devient de fait le registre d'identité sans l'avoir dit, ce qui est
  précisément ce qu'il ne faut pas déclarer à la CDP ; la cascade décès reste inopérante ;
  la révocabilité annoncée du VID reste fictive.
- **Rejetée** : intenable devant le régulateur, et ne débloque pas L1.

### Option C — Registre souverain aux chemins MOSIP, par étapes *(retenue)*

- **Pour** : la cascade décès devient possible immédiatement ; la révocabilité du VID
  devient réelle ; la compatibilité MOSIP est préservée au niveau du contrat, donc la
  bascule ultérieure reste un repointage ; ABIS et IDA restent ouverts pour L2.
- **Contre** : le Sénégal exploite un composant d'identité qu'il maintient lui-même ; la
  base `mosip_kernel` devient un système de référence, ce qui change la criticité de sa
  sauvegarde ; le service n'a pas encore d'authentification applicative.
- **Retenue** : c'est la seule option qui débloque L1 sans mentir sur ce que le dispositif est.

---

## 4. Conséquences

### 4.1 Acquises

- La branche décès du bridge **résout avant de frapper** : pièce d'identité présentée au
  guichet, puis nom et date de naissance. Sujet trouvé, on reprend son UIN et son VID, puis
  on désactive l'identité — ce qui révoque ses VID en chaîne.
- Un décès **non rattaché ou ambigu ne produit aucune frappe** : l'acte est numéroté,
  l'écart est ouvert et exposé. *Un décès non rattaché est une information utile ; un décès
  faussement rattaché à un identifiant neuf est un mensonge propre.*
- Le VID est émis par le registre, donc réellement révocable et rotatif. La génération
  locale devient un mode dégradé explicite, désactivé par défaut et comptabilisé.
- L'attribution est **idempotente par événement** (`compositionId`), ce qui neutralise le
  mode de panne des deux chemins d'attribution concurrents — le même défaut que le double
  DRN corrigé le 19/08, un cran plus haut.
- Un **journal d'allocation** rend visible un numéro frappé jamais confirmé, au lieu de le
  perdre silencieusement.

### 4.2 Dettes créées ou aggravées par cette décision

Ces points sont **la contrepartie directe** de l'option retenue et doivent être suivis :

| # | Conséquence | Traitement |
|---|---|---|
| S1 | Le service expose désormais la **désactivation d'identité**, sur une route publique **sans authentification** | Route retirée de la kustomization le 2026-08-20 ; rétablissement uniquement derrière 3scale |
| S2 | Aucune NetworkPolicy d'ingress ne protégeait le service | `06-networkpolicy-ingress.yaml` ajouté |
| S3 | `identity/search` renvoie l'état civil complet, là où un pointeur suffirait | À réduire avant que le hub ne devienne appelant — contredit la minimisation du §7 |
| S4 | Aucun journal inaltérable des changements de statut d'identité | À traiter avant L1 |
| R1 | `mosip_kernel` devient un **système de référence** ; sa sauvegarde change de criticité | RPO/RTO à requalifier — les sauvegardes CNPG étaient en échec de juin au 19/08 |

### 4.3 À surveiller

- La **dette d'identité** : au moment de la mise en service, le registre comptait 41 numéros
  et aucune identité. Tant que les dossiers antérieurs n'y sont pas versés, la résolution ne
  trouve personne : le mécanisme fonctionne, mais sur un registre vide.
- Le **webhook `mosip-api`** reste un second chemin d'attribution. L'idempotence ne le
  neutralise que s'il présente la même clé — à vérifier, puis à réduire à une notification.
- L'index d'échange (`iun_vid_map`, `iun_event_reg`) vit toujours dans le MongoDB
  d'OpenCRVS. À sortir vers le PostgreSQL du hub pendant L1.

---

## 5. Mise en œuvre

### 5.1 Fait le 2026-08-20

- **`iun-uin-service` v2.1** — trois tables ajoutées (journal d'allocation, identité et
  statut, VID et cycle de vie), treize chemins au format MOSIP, dont
  `POST /idrepository/v1/identity/search` qui rend un **verdict** et ne fusionne jamais de
  lui-même, et deux chemins de reprise (`/v1/admin/uin/import`, `/v1/admin/vid/import`).
  55 assertions vertes contre PostgreSQL 16.
- **`iun-uin-bridge` v4.0** — résolution avant frappe, écriture de l'identité à la
  naissance, désactivation au décès, VID du registre, reprise et disjoncteur, dégradation
  gracieuse, endpoint `GET /ecarts`. 30 assertions vertes contre le registre réel.
- Déploiement et vérification sur Origins (`iun-uin-service-8`, `iun-uin-bridge-22`).

### 5.2 Reste à faire

1. **Rattrapage des identités antérieures** — script prêt, non lancé.
2. Retirer la Route en cluster et appliquer la NetworkPolicy d'ingress.
3. Requalifier la sauvegarde et la reprise de `mosip_kernel`.
4. Réduire `identity/search` à un pointeur.
5. Placer le registre derrière 3scale (dépend de l'arbitrage A4).

---

## 6. Décisions liées

| Réf. | Sujet | Lien avec cet ADR |
|---|---|---|
| **A1** | Format d'identifiant — TOR (9 chiffres) ≠ ADR-R01 (10) ≠ production (UIN 10 + VID 16) | **Dépend de A8.** Devient le point d'arbitrage le plus urgent, et doit être tranché **avant toute montée en volume** : le vivier du générateur fige la longueur, et re-frapper des identifiants déjà distribués n'est pas une option. |
| **A2** | Périmètre de stockage du hub | Inchangé : le hub garde l'index, le journal et les consentements. L'identité est chez le registre, pas chez le hub. |
| **A4** | Passerelle 3scale | Conditionne la levée de S1 et S3. |
| **A5** | Décret ANIU | Conditionne la désignation du responsable de traitement du registre d'identité. |

---

## 7. Ce qui invaliderait cette décision

- La mise à disposition d'un **MOSIP amont opérationnel et supporté** sur Origins, avec
  IdRepo, ABIS et IDA : la décision resterait valable dans son principe, mais le service
  souverain deviendrait un adaptateur, et le basculement se ferait par `IDENTITY_SERVICE_URL`.
- Une décision politique confiant le registre d'identité à un autre opérateur que l'ANIU :
  il faudrait alors rejouer le §4.2, notamment le responsable de traitement.
- L'arbitrage **A1** tranchant sur **9 chiffres** : cela imposerait de re-frapper les
  identifiants déjà distribués, opération dont le coût croît avec le volume — d'où l'urgence
  de le porter en comité.
