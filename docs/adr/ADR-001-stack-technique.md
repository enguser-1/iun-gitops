# ADR-001 — Stack technique cible du programme IUN

- **Statut** : Proposed (à valider en comité technique avant bascule en Accepted)
- **Date** : 2026-05-23
- **Décideurs** : Lead Senior Developer (Alex), Architecte plateforme, Architecte sécurité, Architecte data, SRE, DSI État (validation)
- **Format** : MADR 3.0
- **Programme** : Identifiant Unique National (IUN) — République du Sénégal
- **Maître d'ouvrage** : ANIU *(à confirmer — cf. en-tête du rapport d'évaluation)*
- **Sources normatives** :
  - `C:\IUN_APP\EVALUATION_OPENSHIFT_v4.16.md` v1.0 (2026-05-23) — référencé ci-après par `EVAL §N`
  - `C:\IUN_APP\SYNTHESE_ET_PLAN_DEMARRAGE_v1.md` v1.0 (2026-05-23) — référencé ci-après par `SYN §N`

---

## 1. Contexte

Le programme IUN doit remplacer l'application source `IUN.Api` v3.0, qualifiée par le rapport d'assessment de *« monolithe ASP.NET Core 8 / EF Core mono-instance conçu pour un démonstrateur poste-de-travail »* qui *« ne porte aucune des propriétés requises d'une application productive nationale »* (EVAL §0). La couverture par capacités natives OpenShift ou Operators certifiés est démontrée à hauteur de **27 exigences sur 31 (87 %)** (EVAL §3 / §3.8).

Le verdict du rapport est **GO conditionnel** (EVAL §7) avec cinq conditions cumulatives, dont la condition #5 *« commitment budget Operators payants (RH ACS, RHACM) si retenus »*. Cet ADR fige la **pile technique cible** sur laquelle s'engageront les chantiers de Build (6–9 mois, 8–12 ETP — EVAL §7), sous réserve explicite de la levée des conditions §7 et notamment de la condition souveraineté qui conditionne l'arbitrage KMS, multi-site et choix Operators commerciaux.

Cet ADR couvre **uniquement** les briques structurantes listées en mission. Les briques périphériques mais nécessaires (cache, frontend NGINX, stockage WORM audit, cert-manager, Compliance Operator, Quay) feront l'objet d'ADR ultérieurs (`ADR-002` et suivants).

### Exigences directrices retenues (EVAL §1.2, SYN §1.3 — *hypothèses architecte à valider en comité*)

- Souveraineté : hébergement territoire SN, exploitation opérateur national, clés détenues localement (loi 2008-12).
- Disponibilité : 99,9 % verify, 99,5 % enrôlement.
- Latence P95 : verify checksum < 50 ms, verify identity < 150 ms, enrôlement < 800 ms.
- Débit pic : 2 000 RPS verify, 200 RPS enrôlement.
- Rétention audit : 10 ans en immuabilité.
- RTO ≤ 4 h, RPO ≤ 15 min.
- Open-source strict sur le chemin critique applicatif.

---

## 2. Décision

La plateforme cible IUN s'appuiera sur la pile suivante, alignée sur la stack recommandée par l'assessment (EVAL §2.1, repris sans modification par SYN §2.2) :

| # | Couche | Choix retenu | Mode d'exploitation |
|---|---|---|---|
| 1 | Runtime applicatif | **Quarkus 3.x sur Java 21**, compile **native via Mandrel** | Image OCI rootless, SCC `restricted-v2`, Deployment + HPA |
| 2 | Base de données | **PostgreSQL 16** via **CloudNativePG Operator** | 1 primary + 2 standby, Barman PITR, stockage ODF Ceph RBD |
| 3 | IAM | **Red Hat build of Keycloak 24** (RHBK) | OIDC interne agents, fédération SAML/OIDC vers CNIE/CMU/Wave |
| 4 | Service mesh | **OpenShift Service Mesh 3** (Istio Ambient) | `PeerAuthentication: STRICT`, AuthorizationPolicy, RequestAuthentication JWT |
| 5 | Messaging | **Apache Kafka via AMQ Streams Operator** (Strimzi) | Topics `audit-events`, `enrol-events` ; clé de l'archivage immuable |
| 6 | GitOps | **Argo CD** (OpenShift GitOps Operator) | Monorepo `iun-platform`, Sync-waves, ApplicationSet par environnement |
| 7 | CI | **Tekton** (OpenShift Pipelines Operator) | Pipelines build → scan → sign Cosign → push Quay → trigger Argo CD |
| 8 | Secrets | **HashiCorp Vault** + **External Secrets Operator** (ESO) | Vault local (souverain), ESO synchronise vers `Secret` K8s |
| 9 | Observabilité | **OpenShift Logging 6 (Loki/Vector)** + **Prometheus** + **Tempo** | Logs Loki, métriques Prometheus + UWM, tracing Tempo via OpenTelemetry Operator |
| 10 | Sécurité runtime | **Red Hat Advanced Cluster Security (RHACS)** | Sous réserve de levée explicite de la condition §7 #5 (budget) — à défaut fallback Falco + Trivy (cf. §4.10) |

> **Périmètre de cet ADR** : briques structurantes. Les choix complémentaires (cache Redis/KeyDB, frontend NGINX séparé, NooBaa Object Lock pour l'audit WORM, cert-manager, Compliance Operator, Quay, OADP/Velero, KEDA) sont actés *par renvoi* à EVAL §2.1 et seront formalisés dans des ADRs dédiés.

### Implications transverses non négociables

- **Aucune migration applicative au démarrage** : remplacement de `db.Database.Migrate()` (EVAL §1.3 et §5 risque #5) par un Job Kubernetes idempotent orchestré par Argo CD `Sync-wave: "-1"` (Liquibase ou Flyway côté Quarkus).
- **Frontend découplé** : la SPA n'est plus servie par le back ; image NGINX séparée + Route propre (EVAL §2.3 ; SYN §2.1 L10).
- **Aucun secret en code** : le salt HMAC `"iun-senegal-dev-2026"` (EVAL §5 risque #4, gravité Critique) est migré vers Vault dès Sprint 0 (SYN §2.3 J3).
- **Probes séparées** : startup / readiness / liveness via Quarkus MicroProfile Health (EVAL §2.3, §8).

---

## 3. Justification brique par brique

### 3.1 Runtime — Quarkus 3.x (Java 21, compile native Mandrel)

**Décision** : Quarkus 3.x, compile native via **Mandrel** (distribution Red Hat de GraalVM Community Edition).

**Justification (EVAL §2.1 ligne « Runtime applicatif » + §9 annexe composants)** :
> *« Démarrage < 50 ms, RSS < 100 MB → scale-to-zero possible ; MicroProfile Health/Metrics/OpenTelemetry natif ; first-class citizen OpenShift (Red Hat build of Quarkus supporté). »*

Le rapport place explicitement la performance native sur le chemin critique du PoC #2 verify haut débit (EVAL §5 risque #8, §6 PoC #2) : *« compile native Quarkus + cache Redis pour vérifs identité ; PoC charge »*. Le critère de sortie « *cold start < 1 s, RSS < 100 MB* » (SYN §2.1 L7) est cohérent uniquement avec un runtime natif léger — d'où Quarkus + Mandrel.

**Note importante sur la mission demandée** : la mission Lead Dev mentionne *« build natif GraalVM »*. Le rapport (EVAL §9) recommande explicitement **Mandrel** plutôt que GraalVM Community : *« privilégier Mandrel = RH/OSS »* (parmi les « risques additionnels surveillés » EVAL §5). Mandrel est la distribution Red Hat de GraalVM CE, sans la couche commerciale d'Oracle. Cet ADR retient **Mandrel**, ce qui est techniquement équivalent à GraalVM CE mais évite l'ambiguïté licence GraalVM Enterprise. À acter explicitement si divergence vs. mission Lead Dev.

**Alternatives écartées** :

- **Spring Boot 3 + GraalVM Native** (EVAL §2.1) — *« maturité écosystème plus large, mais empreinte +30 % »*. Écarté car le hot path verify (2 000 RPS, RSS contraint) ne tolère pas un surcoût mémoire de cet ordre, et le différentiel d'écosystème ne compense pas sur ce périmètre.
- **Conservation .NET 8** (existant). Écarté de fait par le verdict « réécriture full open-source » (EVAL §0) et l'exigence open-source strict (EVAL §1.2). Le rapport relève que *« 90 % du code de service est portable, mais 100 % de la couche transverse est à reconstruire »* (EVAL §0) — la portabilité du code métier (Verhoeff pur, DTOs, validations) est garantie côté Java.
- **Node.js / Go**. Non considérés par le rapport ; écartés pour cohérence avec l'écosystème JVM majoritaire des partenaires Camel/EUDI (EVAL §3.6 ; bibliothèque EUDI `eudi-lib-jvm` cible JVM).

---

### 3.2 Base de données — PostgreSQL 16 via CloudNativePG

**Décision** : PostgreSQL 16, déployé et opéré par l'Operator **CloudNativePG** (CNCF, niveau maturité IV — EVAL §4 ligne 10).

**Justification (EVAL §2.1 ligne « Base de données » + §3.3 + §4 Operator #10)** :
> *« Open-source pur, réplication streaming HA, PITR via Barman ; Operator gère bascule primary, backup, certificats. »*

Couvre directement les exigences EVAL §3.3 : stockage bloc HA, snapshots applicatifs cohérents (CSI VolumeSnapshot + OADP), backup PITR (CloudNativePG `Backup` CRD + Barman vers S3 NooBaa local). Cohérent avec la topologie cible 1 primary + 2 standby (EVAL §2.2 diagramme).

Le caractère **CRD CNCF/OSS** de CNPG limite le verrouillage fournisseur — point explicité par EVAL §5 risque #10 : *« CNPG, Strimzi, ESO sont CNCF/OSS »*.

**Alternatives écartées** :

- **Crunchy Postgres for Kubernetes** (EVAL §2.1) — *« supporté Red Hat »* mais ajoute une dépendance commerciale là où CNPG suffit fonctionnellement. Écarté par défaut, peut être réintroduit si le support contractuel est exigé par le DSI.
- **EnterpriseDB community** (EVAL §2.1) — *« éviter EDB community pour ce projet (licence) »*. Écarté pour raison de licence.
- **Conservation SQLite** (existant). Écarté de fait : mono-fichier, incompatible HA / replicas / sauvegarde PITR / multi-instance — c'est précisément l'un des défauts identifiés du démonstrateur (EVAL §0).

---

### 3.3 IAM — Red Hat build of Keycloak 24 (fédération SAML/OIDC)

**Décision** : **Red Hat build of Keycloak 24** (ex-RHSSO), Operator certifié Red Hat (EVAL §4 Operator #9, maturité IV).

**Justification (EVAL §2.1 ligne « Identity & Access » + §3.4 + §3.6 + EVAL §5 risque #3)** :
> *« OIDC pour agents ANIU, fédération possible vers CNIE / IdP consulaire ; SAML pour tiers gouvernementaux ; supporté par Red Hat. »*

Le rapport identifie comme risque **Critique** (EVAL §5 #3) le constat *« grep [Authorize] = 0 occurrences »* dans la source : la couche d'authentification doit être **construite intégralement**, ce qui justifie un IdP managé en standard plutôt qu'une implémentation maison. La fédération SAML/OIDC est requise par la condition §7 #3 : *« cadrage du modèle d'identité fédéré (Keycloak ⇄ CNIE/CMU/Wave) »*.

L'Authorization fine-grain est externalisée vers Istio `AuthorizationPolicy` (EVAL §3.4) — Keycloak fournit l'AuthN, Istio fait respecter l'AuthZ. Le PoC #4 (EVAL §6) valide ce flux de bout en bout avec un mock SAML CNIE.

**Alternatives écartées** :

- **Keycloak community** (sans support) (EVAL §2.1) — Écarté pour un service public national : pas de SLA contractuel sur les correctifs CVE de l'IdP, ce qui est incompatible avec la criticité de la brique d'identité.
- **IdP propriétaire (Okta, Auth0, Azure AD)**. Écarté de fait par l'exigence souveraineté (EVAL §1.2) et open-source strict.
- **Implémentation maison** (extension du code .NET existant). Écartée — risque sécurité majeur, l'identité d'État ne se réinvente pas.

---

### 3.4 Service mesh — OpenShift Service Mesh 3 (Istio Ambient)

**Décision** : **Red Hat OpenShift Service Mesh 3**, mode **Istio Ambient** (EVAL §4 Operator #3, maturité IV).

**Justification (EVAL §2.1 + §3.2 + §3.4 + topologie §2.2)** :
> *« mTLS automatique entre services, autorisations L7, rate limit, observabilité distribuée »* (EVAL §2.1).
> *« mode `PeerAuthentication: STRICT` par namespace »* (EVAL §3.2).

Le mesh est porteur de **trois fonctions transverses** que la mission considère comme non négociables :

1. **mTLS interne strict** (exigence EVAL §1.2 « chiffrement in-transit ») — automatique sans modification applicative.
2. **AuthZ L7** par `AuthorizationPolicy` (EVAL §3.4) : externalise l'autorisation hors code, ce qui est aligné avec la décision IAM (cf. §3.3).
3. **`RequestAuthentication` JWT** (EVAL §2.2 diagramme) : validation des JWT Keycloak au niveau mesh, le service applicatif n'a pas à embarquer la mécanique de vérification de signature.

Le mode **Ambient** (sidecar-less) est explicitement cité par EVAL §2.1 ; il réduit l'empreinte ressources sur le hot path verify (pas de sidecar Envoy par pod), ce qui est consistant avec les cibles RSS du PoC #2.

**Alternatives écartées** :

- **NGINX Ingress + plugin custom** (EVAL §2.1) — *« non recommandé (perte mTLS auto) »*. Écarté car ferait porter à chaque service applicatif la charge du mTLS et de la validation JWT.
- **Linkerd**. Non considéré par le rapport ; non retenu car écosystème moins intégré à OpenShift et absence de Operator certifié Red Hat sur OCP 4.16.
- **Istio communauté (non-OSSM)**. Écarté pour la même raison que Keycloak community : pas de SLA contractuel sur une brique sécurité critique.

---

### 3.5 Messaging — Apache Kafka via AMQ Streams (Strimzi)

**Décision** : **Apache Kafka** opéré par **AMQ Streams Operator** (Strimzi upstream), Operator Red Hat maturité V (EVAL §4 Operator #8).

**Justification (EVAL §2.1 ligne « Messaging asynchrone » + §3.6 + §5 risque #6)** :
> *« Découpler audit / événements d'enrôlement ; rejouable ; clé pour archivage immuable »* (EVAL §2.1).
> *« App publie sur Kafka `audit-events` → archiveur Sink → S3 WORM (NooBaa Object Lock). Corrige le bug existant : AuditLog mutable SQL »* (EVAL §3.4).

Kafka est **structurant** pour l'audit immuable (EVAL §5 risque #6, sévérité H) : c'est l'épine dorsale du pattern *« app → Kafka → S3 Object Lock »* validé par le PoC #3 (EVAL §6). Sans bus rejouable, la propriété de traçabilité 10 ans (EVAL §1.2) ne tient pas.

Strimzi est **CNCF / OSS** (rappelé EVAL §5 risque #10 comme limitant le verrouillage fournisseur).

Couplage prévu avec **KEDA** (Operator #16, EVAL §3.6) : autoscaling event-driven des workers enrôlement selon le lag Kafka.

**Alternatives écartées** :

- **RabbitMQ via Cluster Operator** (EVAL §2.1) — *« si modèle event-driven moins central »*. Écarté car le modèle event-driven *est* central (audit, enrôlements asynchrones, intégration tiers).
- **NATS / Pulsar**. Non considérés par le rapport ; non retenus pour cohérence avec l'écosystème AMQ Streams supporté Red Hat.
- **Conservation SQL pour audit** (existant). Écartée par EVAL §5 risque #6 : *« AuditLog mutable en SQL » → « non-conformité exigence traçabilité 10 ans immuable »*.

---

### 3.6 GitOps — Argo CD (OpenShift GitOps)

**Décision** : **Argo CD**, fourni par l'Operator **OpenShift GitOps**, maturité V (EVAL §4 Operator #1).

**Justification (EVAL §2.1 ligne « Build & CI/CD » + §3.7 + SYN §2.3 J2 + §2.4 dépendances)** :
> *« Cloud-native, immuable, GitOps déclaratif »* (EVAL §2.1).
> *« GitOps déclaratif »* (EVAL §3.7).
> *« À J5, tout déploiement passe par GitOps — plus de kubectl apply manuel »* (SYN §2.3).

Argo CD pilote la séquence de déploiement, notamment via les **Sync-waves** qui orchestrent (i) le Job de migration de schéma (`Sync-wave: "-1"`, EVAL §2.3) puis (ii) le Deployment applicatif. C'est la réponse plateforme au risque H #5 *« auto-migration EF Core au démarrage »* (EVAL §5).

Le format **CRD `Application` / `ApplicationSet`** est CNCF / OSS, ce qui limite le verrouillage (EVAL §5 risque #10).

**Alternatives écartées** :

- **Flux CD**. Non considéré par le rapport ; non retenu car OpenShift GitOps est l'intégration de référence sur OCP 4.16, avec maturité V (la plus haute) attribuée dans le rapport.
- **Pas de GitOps (déploiements impératifs)**. Écarté par SYN §2.3 critère de sortie Sprint 0 : *« plus aucun kubectl apply manuel »*.

---

### 3.7 CI — Tekton (OpenShift Pipelines)

**Décision** : **Tekton**, fourni par l'Operator **OpenShift Pipelines**, maturité IV (EVAL §4 Operator #2).

**Justification (EVAL §2.1 + §3.7)** :
> *« Cloud-native, immuable, GitOps déclaratif »* (EVAL §2.1).
> *« CI pipelines »* (EVAL §3.7).

Pipelines structurés `build → scan (Trivy ou RHACS scanner) → sign (Cosign) → push Quay → bump manifeste Git → Argo CD réconcilie`. La signature Cosign est requise par la politique d'admission (EVAL §3.4 ligne « Politique d'admission (signatures) ») et par l'annexe anti-patterns EVAL §8 *« Cosign verify avant admission (Kyverno) »*.

**Alternatives écartées** :

- **Jenkins** (EVAL §2.1) — *« possible mais hors écosystème OpenShift natif »*. Écarté pour cohérence socle.
- **GitHub Actions / GitLab CI** (runners externes). Non considérés par le rapport ; écartés pour souveraineté (les jobs CI manipulent les artefacts d'identité nationale, ils ne sortent pas du cluster).

---

### 3.8 Secrets — HashiCorp Vault + External Secrets Operator

**Décision** : **HashiCorp Vault** (open-source) + **External Secrets Operator** (CNCF sandbox, Operator maturité III — EVAL §4 Operator #13).

**Justification (EVAL §2.1 implicite + §3.4 ligne « Secrets externalisés » + §5 risque #4 + §8 anti-pattern « Secrets dans le code »)** :
> *« External Secrets Operator (CNCF) + HashiCorp Vault (open-source). Élimine salt hardcodé »* (EVAL §3.4).
> *« Salt HMAC hardcodé `"iun-senegal-dev-2026"` dans VerhoeffService.HashIun → gravité Critique → External Secrets + Vault ; rotation procédure documentée »* (EVAL §5 risque #4).

Vault est aussi le **fournisseur de clés** du chiffrement enveloppe applicatif des PII (AES-GCM via **Vault Transit**, EVAL §3.4 ligne « Chiffrement PII applicatif » ; SYN §2.1 L6).

ESO est explicitement cité comme **CNCF / OSS** (EVAL §5 risque #10), donc compatible avec une éventuelle sortie d'OpenShift vers Kubernetes vanilla.

**Alternatives écartées** :

- **Sealed Secrets** (EVAL §8 — *« sealed-secrets si pas de Vault »*). Acceptable seulement comme repli si Vault n'est pas déployable ; ne fournit pas de rotation, ni de Transit, ni d'audit centralisé des accès secrets.
- **KMS cloud propriétaire** (AWS KMS, Azure Key Vault). Écarté par souveraineté (EVAL §1.2) — les clés doivent être détenues localement.
- **Secrets Kubernetes nus** (sans backend externe). Écarté : `Secret` K8s n'est qu'une chaîne base64 par défaut, et stocker un secret dans Git (même chiffré faiblement) est explicitement banni par EVAL §8 *« jamais de Secret en clair dans Git »*.

---

### 3.9 Observabilité — OpenShift Logging (Loki) + Prometheus + Tempo

**Décision** : Stack triple :

- **Logs** : OpenShift Logging 6 (Vector + LokiStack), Operator Red Hat maturité IV (EVAL §4 #4).
- **Métriques** : OpenShift Monitoring (Prometheus intégré) + User Workload Monitoring (Thanos), natif OCP 4.16 (EVAL §3.5).
- **Tracing** : Tempo Operator + Red Hat build of OpenTelemetry Operator (EVAL §4 #6 et #7).
- **UI** : Cluster Observability Operator (Grafana intégré, EVAL §4 #5).

**Justification (EVAL §3.5 entièrement + §4 Operators #4–7)** :
> Métriques infra : *« Stack Prometheus intégrée OpenShift Monitoring »*.
> Métriques user workloads : *« OpenShift User Workload Monitoring (Prometheus + Thanos) »*.
> Logs : *« Red Hat OpenShift Logging 6 (Vector + LokiStack). Loki = OSS Grafana Labs »*.
> Tracing : *« Red Hat build of OpenTelemetry Operator + Tempo Operator. Tempo = OSS Grafana Labs »*.

Quarkus expose nativement OpenTelemetry (cf. §3.1) ce qui rend l'instrumentation tracing gratuite côté code. L'objectif **99,9 % verify** (EVAL §1.2) impose un management SLO documenté — d'où l'option **Sloth / Pyrra** mentionnée EVAL §3.5 (à acter dans un ADR ultérieur).

L'anti-pattern *« Logs sur disque dans le Pod »* (EVAL §8) est tranché : **stdout → Vector → Loki**.

**Alternatives écartées** :

- **ELK / Elasticsearch** pour les logs. EVAL §3.5 indique Loki comme cible (anciennement Elasticsearch dans les premières versions d'OpenShift Logging, désormais Loki est la stack par défaut sur OCP 4.16) — pas de coût licence Elastic, format LogQL aligné avec Prometheus.
- **Jaeger** pour le tracing. Non retenu — la lignée OpenShift bascule de Jaeger vers Tempo (EVAL §4 #7) ; Tempo est plus scalable (stockage objet).
- **Datadog / New Relic** (SaaS). Écartés par souveraineté (les traces et logs contiennent des références à des PII de citoyens).

---

### 3.10 Sécurité runtime — Red Hat Advanced Cluster Security (RHACS) — **conditionnée**

**Décision (conditionnelle)** : **Red Hat Advanced Cluster Security (Stackrox)**, Operator Red Hat maturité IV (EVAL §4 #19), **sous réserve explicite de levée de la condition #5 du verdict** (EVAL §7) : *« commitment budget Operators payants (RH ACS, RHACM) si retenus »*.

**Justification (EVAL §3.4 + §4 #19 + §7 condition #5)** :
> *« Red Hat Advanced Cluster Security 4 (Operator, payant) ou Trivy Operator (Aqua, OSS). RHACS = standard gouvernemental »* (EVAL §3.4).
> *« Détection runtime (anomalies) : Falco via Operator (CNCF) ou RHACS Runtime. Détecte exec-into-pod, écriture binaires »* (EVAL §3.4).

RHACS couvre quatre fonctions distinctes sur l'axe sécurité : (i) scan d'images CVE, (ii) policy-as-code admission, (iii) network policy generator, (iv) détection runtime (anomalies). C'est le *« standard gouvernemental »* selon le rapport.

**⚠ Point d'attention non levé — à signaler au comité** : la mission demande de figer RHACS dans cet ADR, mais EVAL §7 condition #5 n'est pas levée à la date de rédaction (2026-05-23). Cet ADR retient RHACS **par défaut technique**, avec **plan de repli OSS documenté** :

| Fonction RHACS | Alternative OSS (si condition #5 NON levée) | Coût intégration |
|---|---|---|
| Scan d'images CVE | Trivy Operator (EVAL §3.4) | Faible |
| Admission policy | Kyverno Operator OU Gatekeeper/OPA (EVAL §3.4) | Moyen |
| Détection runtime | Falco via Operator CNCF (EVAL §3.4) | Moyen — corrélation à recâbler |
| Conformité benchmark | Compliance Operator OpenSCAP (EVAL §3.4, déjà retenu en parallèle) | Inclus |

**Action requise** : décision DSI sous 30 jours sur la condition #5, avant gel du socle sécurité runtime. Si NO-GO budget, basculer la décision §3.10 vers la pile OSS (Falco + Trivy + Kyverno) et émettre un ADR amendant cette section.

**Alternatives écartées** :

- **Aucune sécurité runtime** (seulement scan statique CI). Écarté — la détection runtime des `exec-into-pod` et écritures de binaires est requise par EVAL §3.4 sur un système qui manipule des PII de 18 M citoyens.
- **Aqua / Sysdig / Prisma Cloud** (commerciaux concurrents). Non considérés par le rapport ; écartés au profit de RHACS pour alignement écosystème OpenShift, ou Falco pour alignement OSS souverain.

---

## 4. Conséquences

### 4.1 Positives

- **Couverture 87 % par capacités natives/Operator** (EVAL §3.8) — la plateforme cesse d'être un démonstrateur poste-de-travail.
- **Résolution directe de 4 risques C/H** dès Sprint 0 : salt HMAC (#4) via Vault J3, auto-migration EF Core (#5) via Job Sync-wave J4, AuditLog mutable (#6) via Kafka+Object Lock J8–J9, AuthN absente (#3) via Keycloak J3 (SYN §2.3, §2.5).
- **Limitation du verrouillage fournisseur** : les CRDs critiques (CNPG, Strimzi, ESO, Argo CD CRD, Tekton CRD, Istio CRD) sont **CNCF/OSS** et portables hors OpenShift si nécessité politique (EVAL §5 risque #10).
- **Cohérence avec les 5 PoCs** : la stack permet d'exécuter PoC #1 (CNPG), #2 (Quarkus native + Istio + Redis cache), #3 (Kafka + Object Lock), #4 (Keycloak + Istio JWT), #5 (CNPG bascule + OADP).
- **Conformité « open-source strict »** (EVAL §1.2) sur le chemin critique applicatif.

### 4.2 Négatives / coûts

- **Réécriture complète de la couche transverse** (EVAL §0 : *« 100 % de la couche transverse est à reconstruire »*). Budget Build 6–9 mois, 8–12 ETP confirmé.
- **Courbe d'apprentissage Quarkus + Mandrel** pour une équipe potentiellement .NET (EVAL §5 risques additionnels : *« capacité d'équipe Quarkus (formation) »*). Mitigation : formation prévue, et le code métier portable (Verhoeff, validations) limite la pression sur la première montée en compétence.
- **Coût Operators payants** non confirmé : RHACS et RHACM (EVAL §7 condition #5). Plan de repli OSS documenté (cf. §3.10).
- **FIPS-mode cluster** (EVAL §1.2 + §3.8) : coût démarrage non négligeable ; doit être décidé au provisioning du cluster, pas après.
- **Dépendance Vault** : Vault devient un SPOF du chiffrement applicatif (Transit) — doit être déployé en HA dès la pré-production.

### 4.3 Risques résiduels (post-décision)

- L'ADR ne fige **pas** la décision sur l'espace IUN (9 vs 12 digits, EVAL §5 risque #1, §7 condition #4) — c'est une décision **métier**, hors périmètre de cet ADR (un ADR distinct sera produit).
- L'ADR ne fige **pas** la topologie multi-site / DR (EVAL §5 risque #7, EVAL §7 condition #1 souveraineté) — décision DSI requise.
- L'ADR ne fige **pas** le modèle de rôles ANIU pour Keycloak (EVAL §5 risque #3) — à produire au Sprint 0 (SYN §2.3 J8).

---

## 5. Alternatives globales considérées et écartées

### 5.1 « Conservation .NET + ajout Auth/Audit en surcouche »

Écartée par EVAL §0 : *« réécriture ciblée du back-end ; 100 % de la couche transverse à reconstruire »*. L'ajout en surcouche reviendrait à empiler des correctifs sur un démonstrateur sans répondre aux propriétés productives (HA, immutabilité d'audit, multi-instance).

### 5.2 « Kubernetes vanilla + Helm pur »

Écartée par le périmètre programme (cible explicite OpenShift 4.16, EVAL §0). Néanmoins, le choix d'Operators CNCF/OSS (CNPG, Strimzi, ESO, Argo CD, Tekton, Istio) **préserve cette option de repli** politique (EVAL §5 risque #10) : la plupart des CRDs sont portables.

### 5.3 « Stack 100 % Red Hat commerciale » (RHACS + RHACM + Crunchy + ...)

Maximise le support mais maximise aussi la facture et le couplage. La stack retenue est **hybride pragmatique** : composants supportés Red Hat sur les briques critiques (Quarkus, Keycloak, AMQ Streams, OSSM3, GitOps, Pipelines, Logging) ; composants OSS sur les briques où l'OSS est plus portable et moins onéreux (CNPG, ESO, Vault). RHACS et RHACM restent en option sous condition budgétaire (§3.10 et hors périmètre de cet ADR pour RHACM).

### 5.4 « Stack 100 % OSS pure »

Maximise l'indépendance fournisseur, mais sacrifie le support contractuel sur des briques sécurité critiques (Istio sans OSSM, Keycloak community). Inacceptable pour un service public national d'identité (cf. justifications §3.3 et §3.4).

---

## 6. Trous & incohérences remontés en lecture des sources

Conformément à la consigne de mission (« si tu identifies un trou ou une incohérence dans le rapport, remonte-le explicitement »), points relevés lors de la rédaction de cet ADR :

1. **GraalVM vs Mandrel** — *Précision plus qu'incohérence* : la mission Lead Dev demande « build natif GraalVM ». Le rapport (EVAL §5 risques additionnels, EVAL §9 annexe composants) recommande explicitement **Mandrel** plutôt que GraalVM CE. Cet ADR retient Mandrel. À acter en revue si la mission entendait GraalVM CE ou GraalVM Enterprise.
2. **Service mesh — niveau de maturité Ambient sur OCP 4.16** : le rapport mentionne Istio **Ambient** (EVAL §2.1, §3.2). À vérifier en revue technique que le mode Ambient est **GA** sur OSSM 3 fourni avec OCP 4.16 (et non en Tech Preview), faute de quoi un repli sur le mode sidecar serait nécessaire. *Trou : le rapport ne précise pas le statut GA/TP du mode Ambient sur la version OSSM3 livrée avec OCP 4.16.*
3. **RHACS — condition §7 #5 non levée** (cf. §3.10 de cet ADR) : la mission demande de figer RHACS, alors que le rapport place explicitement le commitment budget en condition cumulative non encore levée. ADR émis avec plan de repli OSS documenté ; **arbitrage DSI requis sous 30 jours**.
4. **Volumétries cibles = hypothèses architecte** (EVAL §1.2 explicite : *« aucun document de contrainte n'a été trouvé »*) : les 2 000 RPS verify, 200 RPS enrôlement, 99,9 %, RTO 4 h / RPO 15 min ne sont pas des chiffres validés par l'ANIU. **Risque** : si l'ANIU révise à la hausse, la stack tient (architecturalement bornée par Kafka + HPA + cache + CNPG read replicas), mais les sizings cluster doivent être refaits. À documenter en ADR « capacity planning » ultérieur.
5. **Couverture 27/31 — le rapport ne liste pas explicitement les 31 exigences en regard** : la matrice §3 est organisée par axe (compute, réseau, stockage, sécurité, observabilité, intégration, opérabilité, conformité) mais le décompte « 31 exigences » et les « 4 non couvertes » ne sont pas explicités ligne à ligne. *Trou de traçabilité* : à reconstituer en revue d'architecture.
6. **Maître d'ouvrage ANIU « à confirmer »** (EVAL en-tête + SYN §1.5) : la chaîne décisionnelle politique reste à officialiser pour pouvoir adresser la condition #1 souveraineté (EVAL §7).
7. **Cache (Redis vs KeyDB) non tranché** par le rapport (EVAL §2.1 : *« Redis Enterprise Operator ou KeyDB »*). Non bloquant pour cet ADR (le cache n'est pas dans le périmètre de l'ADR-001) mais à acter dans `ADR-002`.
8. **Pas de chiffrage budgétaire** des Operators payants ni des licences éventuelles (signalé par SYN §4 point 6).
9. **Conventions partenaires** (CNIE / CMU / Wave) absentes du dossier (SYN §4 point 5) — bloquant pour finaliser la configuration Keycloak (claims, mappers SAML) au-delà du PoC #4.
10. **Format IUN (9 vs 12 digits)** non tranché — décision métier hors périmètre ADR-001 mais structurante pour la modélisation données côté CNPG (taille colonne, index, partitionnement éventuel).

---

## 7. Critères de revue & révision

Cet ADR doit être **revu** dans les cas suivants :

- Levée (ou non) de l'une des 5 conditions §7 du verdict EVAL.
- Résultat des PoC #1, #2, #4 (si KO sur PoC #2, re-design avant tout engagement — EVAL §7 NO-GO).
- Décision DSI sur la condition #5 budget (impacte directement §3.10 de cet ADR).
- Décision politique sur la souveraineté hébergement (impacte §3.8 — KMS Vault local ou HSM partenaire).
- Évolution majeure d'OpenShift au-delà de 4.16 EUS (juin 2026, EVAL note de portée).

---

## 8. Références

- `EVAL` — `C:\IUN_APP\EVALUATION_OPENSHIFT_v4.16.md` v1.0 (2026-05-23) — Évaluation de faisabilité technique, 385 lignes.
- `SYN` — `C:\IUN_APP\SYNTHESE_ET_PLAN_DEMARRAGE_v1.md` v1.0 (2026-05-23) — Synthèse Lead Dev + plan de démarrage.
- Code source existant — `C:\IUN_APP\IUN.Api\` (référence d'anti-patterns à corriger).
- MADR format — <https://adr.github.io/madr/> (référence méthodologique du présent ADR).

---

## 9. Annexe — Récapitulatif des Operators à provisionner au Sprint 0

Référence : EVAL §4 Operators recommandés + SYN §2.3 Semaine 1 J3.

| Operator | Source EVAL | Phase Sprint 0 |
|---|---|---|
| OpenShift GitOps (Argo CD) | §4 #1 | J2 |
| OpenShift Pipelines (Tekton) | §4 #2 | Semaine 2 |
| OpenShift Service Mesh 3 | §4 #3 | J3 |
| OpenShift Logging 6 | §4 #4 | J3 |
| Cluster Observability Operator | §4 #5 | Semaine 2 |
| Red Hat build of OpenTelemetry | §4 #6 | Semaine 2 |
| Tempo Operator | §4 #7 | Semaine 2 |
| AMQ Streams | §4 #8 | J3 puis J8 |
| Red Hat build of Keycloak | §4 #9 | J3 |
| CloudNativePG | §4 #10 | J3, premier cluster J4 |
| ODF 4.16 | §4 #11 | J2 (provisioning cluster) |
| cert-manager | §4 #12 | Semaine 2 |
| External Secrets Operator | §4 #13 | J3 |
| Compliance Operator | §4 #14 | Semaine 2 |
| OADP (Velero) | §4 #15 | Sprint 1 |
| KEDA | §4 #16 | Sprint 1 |
| OpenShift Serverless | §4 #17 | Optionnel (diaspora) |
| VPA | §4 #18 | Sprint 1 |
| RHACS *(sous condition §7 #5)* | §4 #19 | Sprint 1, après décision DSI |
| Quay | §4 #20 | Semaine 2 |

---

*ADR-001 v1.0 — 2026-05-23 — Auteur : Lead Senior Developer (Alex). Statut : Proposed, en attente de validation comité technique et arbitrages DSI listés au §6.*
