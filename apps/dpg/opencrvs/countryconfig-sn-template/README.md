# `opencrvs-countryconfig-sn` — Template de personnalisation Sénégal

Ce dossier est un **template documentaire**. Il décrit comment créer le repo
`opencrvs-countryconfig-sn` qui contiendra la configuration pays Sénégal pour
OpenCRVS. Il ne contient pas la configuration complète — celle-ci sera développée
par l'équipe IUN avec l'expertise état civil sénégalaise (DGEAT, Cour suprême,
tribunaux d'instance) et iterée.

> **Référence TOR** : §3.2 C1, §4.2 — OpenCRVS Sénégal, PWA hors-ligne FR/WO/PF,
> 14 régions + 46 départements, code de la famille sénégalais, déclaration tardive
> art. 54.

---

## 1. Procédure de fork

OpenCRVS impose un **fork par pays** du template officiel `opencrvs-countryconfig`,
maintenu sur la même version que `opencrvs-core` (règle upstream : "always update
infrastructure to same version as OpenCRVS Core" — README officiel).

### 1.1 Pré-requis

| Outil | Version | Vérification |
|---|---|---|
| `gh` CLI | >= 2.40 | `gh --version` |
| `git` | >= 2.40 | `git --version` |
| Node | 18.x (cf. `.nvmrc` upstream) | `node --version` |
| Yarn | 1.22.x | `yarn --version` |
| Accès org | `enguser-1` | `gh auth status` |

### 1.2 Création du fork (PowerShell)

```powershell
# Variables
$Org      = 'enguser-1'
$Repo     = 'opencrvs-countryconfig-sn'
$Upstream = 'opencrvs/opencrvs-countryconfig'
$Version  = 'v1.9.13'  # MUST match opencrvs-core version pinned in apps/dpg/opencrvs/application.yaml

# 1) Fork via gh (option A : fork direct du repo upstream — préserve l'historique)
gh repo fork $Upstream --org $Org --clone --remote --fork-name $Repo

# 2) Bascule sur la branche release alignée avec opencrvs-core
Set-Location $Repo
git fetch upstream --tags
git checkout -b "release/$Version" "$Version"
git push origin "release/$Version"

# 3) Branche pays (configuration vivante du Sénégal)
git checkout -b 'country/sn' "release/$Version"
git push -u origin 'country/sn'
```

> **Alternative — Option B (repo from scratch)** : ne pas forker, créer un repo
> vide et copier seulement les fichiers source à modifier. Avantage : historique
> propre. Inconvénient : perte du suivi upstream et des PR de sécurité.
> **Recommandation Architecte** : option A (fork direct).

### 1.3 Convention de branches

| Branche | Rôle |
|---|---|
| `country/sn` | Branche vivante de la config Sénégal — base des MR |
| `release/v1.9.x` | Branche de tracking upstream — rebase régulier |
| `feat/<ticket>` | Branches de travail individuelles |
| `hotfix/<ticket>` | Correctifs urgents prod |

---

## 2. Fichiers / paths à personnaliser

Liste non exhaustive — basée sur la structure standard `opencrvs-countryconfig`
(template "Farajaland"). À chaque release `v1.9.x`, vérifier le diff upstream.

### 2.1 Hiérarchie administrative

| Path | Rôle | À adapter pour SN |
|---|---|---|
| `src/data-seeding/locations/source/locations.csv` | Liste plate régions + sub-divisions | **Oui** — voir `hierarchy-skeleton.csv` (14 régions + 46 départements + 552 communes) |
| `src/data-seeding/locations/source/statistics.csv` | Population + taux de natalité/mortalité par admin level 1 | **Oui** — source ANSD (Recensement 2023, projections 2026) |
| `src/data-seeding/locations/source/health-facilities.csv` | Établissements de santé (maternités → enrôlement nouveau-né) | **Oui** — source MSAS / Carte sanitaire |
| `src/api/application/application-config.ts` | Niveaux d'admin (1 à 5), libellés FR/WO/PF | **Oui** — Région, Département, Arrondissement (optionnel), Commune, Quartier |

### 2.2 Formulaires (événements d'état civil)

| Path | Événement | À adapter pour SN |
|---|---|---|
| `src/form/birth/index.ts` | Naissance | **Oui** — code famille art. 50-65 (déclaration < 1 an par parents, > 1 an = tardive avec jugement supplétif) |
| `src/form/death/index.ts` | Décès | **Oui** — code famille art. 81-91 |
| `src/form/marriage/index.ts` | Mariage | **Oui** — code famille art. 100-150 (monogamie/polygamie au choix de l'époux, dot, témoins) |
| `src/form/divorce/` (à créer si absent) | Divorce | **À créer** — code famille art. 157-187 (divorce judiciaire uniquement) |
| `src/form/legalization/` (custom) | Légalisation tardive (NIA → IUN) | **À créer** — flow TOR §4.12 / §3.4.2, jugement supplétif tribunal d'instance |

### 2.3 Certificats imprimables (templates SVG)

| Path | Rôle | À adapter |
|---|---|---|
| `src/data-seeding/certificates/source/birth-certificate.svg` | Acte de naissance | **Oui** — design ANIU, en-tête République du Sénégal, devise, sceau, QR code |
| `src/data-seeding/certificates/source/death-certificate.svg` | Acte de décès | **Oui** — idem |
| `src/data-seeding/certificates/source/marriage-certificate.svg` | Acte de mariage | **Oui** — idem |

> Sceau électronique conforme **eIDAS-équivalent CEDEAO** (TOR §5.3 — signature
> qualifiée).

### 2.4 Rôles et bureaux

| Path | Rôle | À adapter |
|---|---|---|
| `src/data-seeding/employees/source/employees.csv` | Comptes test agents enregistreurs | **Oui** — bureau test par région pilote (Dakar, Thiès) |
| `src/api/data-generator/` | Données de seed (dev/staging) | **Oui** — noms wolof/pulaar plausibles, distribution démographique SN |
| `src/api/application/scopes.ts` | Scopes RBAC | À harmoniser avec RHBK realm `iun` (cf. mémoire `[[project-iun-status]]` §RBAC) |

### 2.5 Internationalisation

| Path | Rôle | À adapter |
|---|---|---|
| `src/translations/client.csv` | Libellés UI client (PWA) | **Oui** — colonnes : `id`, `description`, `en`, **`fr`**, **`wo`**, **`ff`** (PF = Pulaar/Fula) |
| `src/translations/notification.csv` | Modèles SMS/email | **Oui** — 4 langues |

> **Important** : OpenCRVS attend des codes ISO 639-1 pour les langues. Wolof =
> `wo`, Pulaar (Fula) = `ff`. Pas de `pulaar` ni `pf`.

### 2.6 Intégration MOSIP / IUN

| Path | Rôle | À adapter |
|---|---|---|
| `src/api/integrations/mosip/` (à créer) | Hook OpenCRVS → MOSIP UIN Generator | **À créer** — voir §5 ci-dessous |
| `src/api/notification/handler.ts` | Notifications post-enregistrement | Ajouter trigger Kafka vers MOSIP |

---

## 3. Hiérarchie administrative SN (canevas)

Voir `hierarchy-skeleton.csv` pour le squelette des 14 régions + 46 départements
avec codes ANSD et ISO 3166-2.

**Niveaux retenus pour OpenCRVS SN** (5 niveaux — max supporté par OpenCRVS) :

| Level | Nom FR | Nom WO | Nom PF | Cardinalité | Source |
|---|---|---|---|---|---|
| 1 | Région | Diiwaan | Diiwal | 14 | ISO 3166-2:SN |
| 2 | Département | Départemaa | Départemaa | 46 | ANSD |
| 3 | Arrondissement (optionnel) | Aroñdiseemaa | — | ~123 | ANSD (groupement de communes) |
| 4 | Commune | Komin | Komin | ~557 (110 urbaines + 447 rurales) | Loi 2013-10 Acte III décentralisation |
| 5 | Quartier / Village (optionnel) | Gokk | Wuro | ~14 000+ | ANSD / Recensement |

> **Décision archi recommandée** : utiliser **niveaux 1, 2, 4** comme requis,
> niveaux 3 et 5 optionnels (`required: false`) pour ne pas bloquer la saisie
> rurale où la donnée est manquante.

---

## 4. Exigences linguistiques (TOR §4.2)

OpenCRVS supporte le multilinguisme via le fichier `client.csv`. Mapping minimal
à produire au démarrage :

| Locale | ISO 639-1 | Couverture sprint 0 | Cible M9 (P2) |
|---|---|---|---|
| Français | `fr` | UI complète + certificats | 100 % |
| Wolof | `wo` | UI core (saisie, recherche, navigation) | UI complète |
| Pulaar | `ff` | Libellés clés + messages erreur | UI core |
| Anglais | `en` | Désactivé en prod (template upstream) | Désactivé |

**Recherche phonétique** : `client.csv` ne suffit pas. Configurer en plus
l'analyzer Elasticsearch :

```yaml
# Snippet à intégrer dans le chart deps (ES custom analyzer)
analysis:
  analyzer:
    senegalese:
      type: custom
      tokenizer: standard
      filter:
        - lowercase
        - asciifolding   # gère accents fr (é, è, à, ç)
        - wolof_phonetic # custom — à développer (double-metaphone n'est pas calibré pour le wolof)
```

> **Action recherche** : commander un mini-rapport linguistique
> wolof/pulaar à un partenaire académique (UCAD, Khouroum) pour calibrer les
> règles phonétiques. Hors périmètre de ce sprint.

---

## 5. Types d'événements à supporter

Référence : **Code de la famille du Sénégal** (Loi n°72-61 du 12 juin 1972,
plusieurs fois amendée — dernière révision majeure 2020).

| Événement | Articles | Implémentation OpenCRVS | Statut sprint 0 |
|---|---|---|---|
| **Naissance** | art. 50-65 | `src/form/birth/` upstream + custom flags FR/WO/PF | **MVP** |
| **Naissance tardive** (> 1 an) | art. 54 | Workflow custom : NIA provisoire + jugement supplétif tribunal d'instance | **Iter 2** |
| **Décès** | art. 81-91 | `src/form/death/` upstream | Iter 2 |
| **Mariage** | art. 100-150 | `src/form/marriage/` — gérer monogamie / polygamie (option époux) | Iter 3 |
| **Divorce judiciaire** | art. 157-187 | Form custom — référence à jugement obligatoire | Iter 3 |
| **Légalisation tardive** | (proc. tribunal) | Workflow NIA → IUN avec validation jugement | Iter 4 |
| **Reconnaissance paternelle / adoption** | art. 196-235 | Hors scope MVP | Backlog |
| **Acte de mariage coutumier** | art. 132 | Hors scope MVP — sensibilité culturelle, à arbitrer DGEAT | Backlog |

---

## 6. Intégration MOSIP — déclenchement IUN à la création de naissance

Référence TOR : **§3.4.1 — Enrôlement nouveau-né maternité → OpenCRVS → MOSIP →
OpenIMIS** (ouverture droits CMU automatique).

### 6.1 Point d'intégration côté OpenCRVS

Hook à brancher dans le service **`webhooks`** (cf. liste des microservices,
README dossier parent §Composants déployés).

**Trigger** : événement `EVENT_REGISTERED` sur un événement de type `BIRTH`,
quand `legalStatus.REGISTERED.acceptedAt` est posé (= ressource FHIR Patient
créée dans Hearth).

### 6.2 Payload MOSIP

API cible : MOSIP **Pre-Registration → ID Repository → UIN Generator** (kernel).

```
POST {{MOSIP_API_GW}}/v1/idrepository/identity
Authorization: Bearer <oauth2-client-credentials>
Content-Type: application/json

{
  "id": "mosip.identity.create",
  "version": "v1",
  "requesttime": "2026-05-24T10:30:00.000Z",
  "request": {
    "identity": {
      "fullName":   [{"language":"fra","value":"<from FHIR.Patient.name>"}],
      "dateOfBirth": "<FHIR.Patient.birthDate>",
      "gender":      [{"language":"fra","value":"<FHIR.Patient.gender>"}],
      "addressLine1":[{"language":"fra","value":"<commune>"}],
      "region":      [{"language":"fra","value":"<region>"}],
      "province":    [{"language":"fra","value":"<departement>"}],
      "city":        [{"language":"fra","value":"<commune>"}],
      "registrationId": "<opencrvs.event.id>",
      "civilRegistrationNumber": "<opencrvs.registrationNumber>"
    }
  }
}
```

**Réponse attendue** :

```json
{
  "response": {
    "uin": "SN-XXXX-XXXX-X",   // 9 chiffres + Verhoeff, format affichage cf. [[project-iun-status]]
    "status": "ACTIVATED"
  }
}
```

### 6.3 Persistance côté FHIR

L'IUN reçu de MOSIP est stocké dans Hearth comme **Patient.identifier** :

```json
{
  "use": "official",
  "system": "https://anie.sn/iun",
  "value": "SN-1234-5678-3",
  "type": {
    "coding": [{
      "system": "https://anie.sn/identifier-type",
      "code": "IUN",
      "display": "Identifiant Unique National"
    }]
  }
}
```

> **Note souveraineté** (cf. mémoire `[[project-iun-status]]`) : seuls le **hash
> SHA-256** de l'IUN et les **8 chiffres aléatoires** restent côté MOSIP. La
> **clé Verhoeff** est recalculée à la lecture. OpenCRVS ne stocke que l'IUN
> formaté affiché — pas de salt applicatif (déprécié, voir mémoire).

### 6.4 Mode dégradé (cluster offline / MOSIP down)

* La déclaration de naissance **n'est PAS bloquée** si MOSIP est inaccessible.
* OpenCRVS génère un **NIA** (Numéro d'Identification d'Attente — TOR §4.12).
* Un job de rattrapage (`webhooks` service, cron 5 min) rejoue les naissances
  sans IUN définitif.

---

## 7. Ordre des travaux recommandé (Sprint 0 → P2)

| Sprint | Livrable countryconfig-sn | Dépendance |
|---|---|---|
| S0 | Fork créé, branche `country/sn`, hierarchy.csv (14 régions seulement, dépts vides), 1 office test à Dakar, locale `fr` UI | — |
| S1 | hierarchy.csv complet (46 dépts), `wo`/`ff` libellés clés, certificat naissance v1 | S0 |
| S2 | Formulaire naissance custom art. 54, workflow NIA | S1 + MOSIP kernel up |
| S3 | Hook MOSIP webhook + scope IUN + analyzer ES sénégalais v1 | S2 + MOSIP API GW |
| S4 | Décès + Mariage formulaires de base | S3 |
| P2 (M5–M9) | Divorce + Légalisation tardive + OpenIMIS callback | S4 + OpenIMIS |

---

## 8. Quand ce template devient le repo réel

Quand l'équipe IUN crée le repo réel `enguser-1/opencrvs-countryconfig-sn` :

1. **Mettre à jour** `apps/dpg/opencrvs/values/dev.yaml`, clé
   `global.countryConfig.repoURL`, avec l'URL du repo.
2. Tagger le repo `v0.1.0-sn` au premier déploiement réussi.
3. Supprimer ce dossier `countryconfig-sn-template/` du `iun-gitops` ou le
   garder comme **documentation de référence** (recommandé : le garder).

---

## 9. Références

* **TOR officielle v2.0** : `proposition_IUN_Senegal_vf.docx`, §3.2 C1, §3.4.1,
  §4.2, §4.12, §5.5 P2.
* **Documentation OpenCRVS countryconfig** :
  <https://documentation.opencrvs.org/setup/3.-installation/3.2-set-up-your-own-country-configuration/3.2.1-fork-your-own-country-configuration-repository>
* **Doc admin divisions OpenCRVS** :
  <https://documentation.opencrvs.org/setup/3.-installation/3.2-set-up-your-own-country-configuration/3.2.2-set-up-administrative-address-divisions>
* **Template upstream** : <https://github.com/opencrvs/opencrvs-countryconfig>
  (Farajaland) — release alignée `v1.9.13`.
* **ISO 3166-2:SN** : <https://en.wikipedia.org/wiki/ISO_3166-2:SN> (14 régions).
* **Départements** : <https://en.wikipedia.org/wiki/Departments_of_Senegal>
  (46 départements, dernier ajout Keur Massar mai 2021).
* **Code de la famille SN** : Loi n°72-61 du 12 juin 1972 (révisions ultérieures
  consolidées par le Ministère de la Justice).
