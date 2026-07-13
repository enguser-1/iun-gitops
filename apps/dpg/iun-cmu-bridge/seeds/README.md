# Seeds OpenIMIS pour le bridge CMU (reproductibles)

Scripts copies depuis `C:\IUN_APP\sprint-3-data-senegal\` (source de verite ici).
A rejouer dans l'ordre apres un reset OpenIMIS, AVANT de demarrer iun-cmu-bridge :

1. `seed_cmu_v1.py` — produit CMU : id 15, code `CMUENF` (gratuite 0-5 ans,
   premium 0, national) + officier id 48 `IUNAUTO`.
2. `seed_cmu_user.py` — InteractiveUser `iun-cmu-bridge` + role avec droits
   insuree/policy via UserRole (ATTENTION : invalider le cache Redis des rights
   apres le grant, cf. memoire openimis-graphql-server-client).

Le mot de passe du bridge est fourni via le Secret `iun-cmu-bridge-creds`
(VaultStaticSecret, cf. ../01-vaultstaticsecret.yaml et ../README-secret.md) —
ne JAMAIS le committer ici.

Idempotence : les deux scripts verifient l'existant avant d'inserer.
Dedup historique : 40 polices doublons soft-deleted le 2026-06-13 (1 police
active par famille) — verifier `policiesByInsuree` avant tout re-seed massif.
