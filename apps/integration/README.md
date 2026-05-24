# apps/integration — Couche d'intégration IUN ↔ systèmes nationaux

> **Statut** : placeholder. Première itération attendue après stabilisation des 3 DPG.

## Périmètre

Cette section du repo hébergera, en phase 2, les composants qui **relient les DPG
internes** (MOSIP, OpenCRVS, OpenIMIS) **aux systèmes nationaux existants** et aux
**partenaires externes**.

Architecture cible (TOR §3.3.2 + §5.4 + §5.6.3) :

```
   Citoyen / Agent / Partenaire
            │
            ▼
   ┌──────────────────────┐
   │  Red Hat 3scale API  │   ◄── TOR §3.3.2 + §5.4
   │  Management (gateway)│         (rate limit, OIDC, audit, portail partenaires)
   └──────────────────────┘
            │
            ▼
   ┌──────────────────────────────────────────────┐
   │  Couche d'intégration (FastAPI / Camel K)    │  ◄── TOR §5.6.3
   │  - Adapters NINA / IPRES / CSS / CMU / DGI   │
   │  - Adapters Orange Money / Wave              │
   │  - Webhook routing entre DPG                 │
   └──────────────────────────────────────────────┘
            │
            ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ MOSIP    │ │ OpenCRVS │ │ OpenIMIS │
   └──────────┘ └──────────┘ └──────────┘
```

## Composants prévus (à venir)

| Composant | Outil envisagé | Rôle |
|---|---|---|
| 3scale Gateway | Red Hat 3scale (Operator OCP) | Gateway officielle TOR §3.3.2 |
| Adapter NINA | FastAPI ou Camel K | Connecteur DGEAT (NINA → IUN, étape d'unification) |
| Adapter IPRES | FastAPI ou Camel K | Sync hebdomadaire affiliations IPRES |
| Adapter CSS | FastAPI ou Camel K | Vérification temps réel CSS |
| Adapter CMU | FastAPI ou Camel K | Sync quotidienne CMU/MSAS |
| Adapter DGI | FastAPI ou Camel K | Fiscalité — partenaire (TOR §3.3.2) |
| Adapter Orange Money | FastAPI | Paiements mobiles (TOR §4.4) |
| Adapter Wave | FastAPI | Paiements mobiles (TOR §4.4) |
| Bus événementiel | AMQ Streams (Kafka) existant | Notifications inter-DPG (TOR §3.4.2) |
| Webhook router | Camel K ou Quarkus mini | Orchestration OpenCRVS → MOSIP → OpenIMIS |

## Pourquoi pas dans `apps/dpg/<dpg>/` ?

Les intégrations sont **transverses** : un adapter NINA expose une route 3scale,
appelle MOSIP, écrit dans OpenIMIS. Le mettre dans le dossier d'un des trois DPG
serait arbitraire. On les regroupe donc ici pour clarifier la responsabilité
d'orchestration et de gouvernance des APIs.

## Référence stratégique

Voir `C:\IUN_APP\architecture\REPENSEE-PROJET-v1.md` §4 (architecture cible)
et `C:\IUN_APP\architecture\INTEGRATION-SYSTEMES-EXISTANTS-v1.md` (analyse du
guide Accel Tech FastAPI, désormais reconnu comme la **couche d'intégration**
légère entre DPG et systèmes existants — pas un concurrent du runtime DPG).

## Backlog T+2 / T+3

- Décider 3scale **on-prem OCP** (Operator officiel) vs **3scale SaaS Red Hat Cloud Services**.
- Choisir runtime adapter : **FastAPI** (cohérent avec le guide Accel Tech) ou **Camel K** (cohérent avec écosystème Red Hat sur OCP). Comparaison TCO + skills.
- Identifier les **endpoints réels** des systèmes nationaux (NINA, IPRES, CSS, CMU, DGI) — passage par les Directions concernées. Cf. lots d'engagement TOR §5.5.
