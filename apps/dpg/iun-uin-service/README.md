# iun-uin-service — registre souverain d'identite

Depuis l'**ADR-R02** (2026-08-20), ce service n'est plus un simple generateur de
numeros : il est le **registre souverain d'identite** du programme IUN. Il detient
l'identite derriere le numero, emet les VID et porte le cycle de vie de
l'identifiant.

Les chemins exposes restent ceux de **MOSIP**. C'est un choix delibere : le jour ou
le kernel MOSIP amont fonctionne, le basculement est un repointage d'URL cote
appelant, pas une reecriture. Voir `architecture/decisions/ADR-R02-mosip-registre-souverain.md`.

## Sources

Les sources vivent desormais **dans ce depot**, sous `src/`. Jusqu'au 2026-08-20
elles n'existaient que sur un poste de travail : le systeme de reference de
l'identite nationale n'avait ni historique, ni revue, ni sauvegarde hors poste.

```
src/
  app/
    main.py       API FastAPI — chemins MOSIP
    db.py         acces PostgreSQL (asyncpg), schema et requetes
    vid.py        generation de VID 16 chiffres (regles MOSIP + Verhoeff)
    generator.py  generation d'UIN 10 chiffres
    verhoeff.py   cle de controle Verhoeff (dihedral D5)
    filters.py    filtres de conformite MOSIP sur l'UIN
  Dockerfile
  requirements.txt
  test_scenario.py   37 assertions — cycle complet
  test_import.py     18 assertions — reprise d'identifiants existants
```

## API

| Chemin | Role |
|---|---|
| `POST /v1/idgenerator/uin` | Frappe un UIN. `idempotencyKey` : deux chemins concurrents qui presentent la meme cle recoivent le meme numero. `reserve` : l'appelant confirme apres avoir ecrit de son cote. |
| `POST /v1/idgenerator/reservation/{id}/confirm` · `/release` | Reglement d'une reservation. |
| `GET /v1/idgenerator/uin/{uin}/validate` · `/explain` | Controle Verhoeff et filtres. |
| `POST /v1/vidgenerator/vid` | Emet un VID pour un UIN (`rotate` pour en changer). |
| `GET` · `PATCH /v1/vidgenerator/vid/{vid}` | Resout, revoque. |
| `POST /idrepository/v1/identity` | Cree l'identite derriere le numero. |
| `GET` · `PATCH /idrepository/v1/identity/uin/{uin}` | Lit, met a jour, **desactive**. |
| `POST /idrepository/v1/identity/search` | **Resout avant de frapper.** Rend un verdict : `RESOLVED`, `RESOLVED_DEMOGRAPHIC`, `AMBIGUOUS`, `WEAK`, `NOT_FOUND`. Ne fusionne jamais de lui-meme. |
| `GET /v1/admin/reservations/stale` | Numeros frappes jamais confirmes. **Doit rester a zero.** |
| `POST /v1/admin/uin/import` · `/v1/admin/vid/import` | Reprise des identifiants distribues avant que le registre n'existe. |

## Deux regles a ne pas perdre

**Un numero deja rendu au citoyen ne se recycle ni ne s'invalide.** C'est pourquoi
la reprise importe les VID deja imprimes sur les actes au lieu d'en emettre de
neufs — meme regle que le compteur de DRN cote bridge.

**La resolution ne tranche jamais seule.** `search` rend des candidats classes et
un verdict ; c'est a l'appelant de decider, et de verser en ecart ce qui reste
ambigu. Un deces non rattache est une information utile ; un deces faussement
rattache a un identifiant neuf est un mensonge propre.

## Exposition

Le service **n'a pas de Route publique** depuis le 2026-08-20 (cf. commentaire en
tete de `kustomization.yaml`) et son ingress est restreint par
`06-networkpolicy-ingress.yaml`. Il n'a **aucune authentification applicative** :
c'est une dette assumee et suivie (S1/S3 du registre de dette), a lever en le
placant derriere 3scale avec jeton, finalite declaree et journalisation.

## Tests

Contre un vrai PostgreSQL 16 :

```bash
export DB_HOST=... DB_PORT=5432 DB_NAME=mosip_kernel DB_USER=postgres DB_PASSWORD=...
python -m uvicorn app.main:app --port 8099 &
python test_scenario.py   # 37 assertions
python test_import.py     # 18 assertions
```

## Pieges rencontres

- `$2` a la fois `VARCHAR` et compare a du texte -> `AmbiguousParameterError`.
  Caster explicitement : `$2::varchar`, `$2::text`.
- **asyncpg rend le JSONB en chaine** par defaut : poser un `set_type_codec` dans
  le `init=` du pool, et alors passer des **dicts** aux parametres jsonb — plus de
  `json.dumps` cote appelant, sinon double encodage.
