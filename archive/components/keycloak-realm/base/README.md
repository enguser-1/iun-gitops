# components/keycloak-realm/base — Placeholder

À remplir phase 2 — Realm `iun` sur RHBK (Red Hat Build of Keycloak).

RHBK Operator est déjà installé sur le cluster (autre équipe). On déploie ici uniquement les CR métier (`Keycloak`, `KeycloakRealmImport`, `KeycloakClient` éventuels selon CRDs RHBK v26), pas la Subscription.

## À définir avant remplissage

- Backend DB : pointer sur le Cluster CNPG IUN (cf. `../../cnpg-cluster/`).
- TLS : cert-manager (déjà installé) + Route OCP reencrypt.
- Clients OIDC initiaux : `iun-api`, `argocd-iun`, `kafka-ui` (si déployé).
- Federation : à voir avec l'équipe IAM (LDAP ministère ?).
