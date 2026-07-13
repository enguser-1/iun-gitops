# iun-uin-bridge — pont UIN/BRN/DRN (OpenCRVS → iun-uin-service)

Poller FastAPI : detecte les Task REGISTERED dans Hearth, mint l'UIN (10 chiffres
Verhoeff via iun-uin-service, format `SN-XXXX-XXXX-XX`), formate BRN `RRR-YYYY-NNNNNN`
/ DRN `RRR-YYYY-DNNNNNN` (sequences atomiques Mongo par region+annee), ecrit les
identifiants sur le Patient FHIR. Sert aussi `/records` (registre HTML) et
`/certificate/birth/{patient_id}` (SVG rempli + QR de verification).

## Layout du dossier

| Chemin | Contenu |
|---|---|
| `01-...` a `05-...yaml` + `kustomization.yaml` | manifests historiques (v1, 2026-06-12) — **superedes par le chart** |
| `chart/` | chart Helm complet (source de verite du deploiement) — copie synchronisee de `C:\IUN_APP\cowork\helm\iun-uin-bridge` |
| `src/` | source de l'application (main.py, Dockerfile, birth-certificate.svg) — copie synchronisee de `C:\IUN_APP\cowork\build` |

## Deploiement

```
helm upgrade --install iun-uin-bridge chart/ -f chart/values-dev.yaml -n iun-opencrvs-dev
# build binaire (le BuildConfig est cree par le chart) :
oc start-build iun-uin-bridge --from-dir=C:\IUN_APP\cowork\build -n iun-opencrvs-dev
```

En prod : digest-pinning obligatoire (`image.digest` dans values-prod.yaml).

## Versions

- v2.5 (2026-07-12) : Birth + Death, QR, /records, /certificate
- v2.6 (2026-07-13) : fix noms (given vides -> double espace), layout cert
  (« Delivre le {date} a » + lieu seul sur sa ligne). Fonts pdfmake cote
  countryconfig (Noto Sans, proxy /fonts) — cf. opencrvs-iun/10-countryconfig-sn.yaml.
