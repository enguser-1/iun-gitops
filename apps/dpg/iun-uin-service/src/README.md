# IUN UIN Service

MVP custom microservice replacing MOSIP `kernel-idgenerator-service` for Sénégal UIN generation.

**Decision pivot 2026-06-03** : after 7h debugging cumulés sur MOSIP charts upstream (9 bugs documentes #M1-#M5 + classpath/SCC/OOM/DNS), pivot strategic vers ce service Python custom. Drop-in replacement : meme API endpoint `POST /v1/idgenerator/uin`, integrable plus tard avec MOSIP kernel reel (Sprint 4+).

## Spec

| Element | Valeur | Source |
|---|---|---|
| Longueur UIN | 10 digits | ADR-R01 + MOSIP defaut |
| Check digit | Verhoeff (Dihedral D5) | MOSIP kernel-idvalidator-uin |
| Premier digit | 2-9 (pas 0/1) | mosip.kernel.uin.not-start-with |
| Restricted | pas 666, pas 786 | mosip.kernel.uin.restricted-numbers |
| Sequence max | 3 (ASCN ou DESC) | mosip.kernel.uin.length.sequence-limit |
| Repeating digit | max 2 consecutifs | mosip.kernel.uin.length.repeating-limit |
| Repeating block | pas de bloc 2+ repete | mosip.kernel.uin.length.repeating-block-limit |
| Palindrome | pas 5+ digits palindromiques | mosip.kernel.uin.length.reverse-digits-limit |
| Frequence | pas 5+ fois meme digit | mosip.kernel.uin.length.digits-limit |
| Even chain | max 3 chiffres pairs consecutifs | mosip.kernel.uin.length.conjugative-even-digits-limit |

## API

```
POST /v1/idgenerator/uin
  -> 200 { uin, length, check_digit, spec, attempts }

GET /v1/idgenerator/uin/{uin}/validate
  -> 200 { uin, valid_check_digit, passes_filters, exists_in_registry }

GET /v1/idgenerator/uin/{uin}/explain  (debug)
  -> 200 { uin, verhoeff_valid, filters: {...} }

GET /actuator/health      -> { status: UP/DOWN, db_reachable, issued_count }
GET /actuator/readiness   -> 200 / 503
```

## Deploy

```powershell
cd C:\IUN_APP\sprint-3-scripts\iun-uin-service
.\Deploy-IunUinService.ps1
```

Sequence : apply manifests -> oc start-build (~3-5 min) -> wait rollout -> smoke test 10 UIN.

## Architecture

```
[Route OCP edge TLS]
  -> [Service ClusterIP 8080]
    -> [Deployment 2x iun-uin-service]
      -> [CNPG mosip-postgres-rw:5432]
         DB = mosip_kernel
         Table = iun_uin_issued (auto-created at startup)
```

Persistence dans la meme DB que MOSIP kernel pour facilite future migration drop-in. Table separee `iun_uin_issued` pour ne pas polluer schemas MOSIP existants.

## Test local

```bash
pip install -r requirements.txt
export DB_HOST=localhost DB_PASSWORD=test
uvicorn app.main:app --reload
curl -X POST http://localhost:8080/v1/idgenerator/uin
```

## Sprint 4 migration plan

Quand MOSIP kernel-idgenerator fonctionne :
1. Migrer table `iun_uin_issued` -> table MOSIP `uin` (DML migration)
2. Repointer consumers (Route alias + DNS preserves)
3. Garder ce service en hot-standby fallback Sprint 5

## References

- `architecture/research/verhoeff/MOSIP-UIN-FEASIBILITY.md` (Sprint 1)
- `architecture/adr/ADR-R01-uin-length-10.md`
- `architecture/sprint-2/WEEK-2-CLOSEOUT.md` §8 (bugs MOSIP charts)
