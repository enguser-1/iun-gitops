# Note d'architecture — Politique des identifiants du programme IUN Sénégal

**Destinataires** : CDP, ANIU · **Rédaction** : équipe plateforme IUN · **Date** : 15 juillet 2026 · **Statut** : appliqué en environnement pilote (Heritage), à valider pour la cible

## Objet

Cette note documente la politique de génération, de stockage et d'affichage des identifiants
délivrés par la plateforme IUN (état civil OpenCRVS + socle d'identité MOSIP), telle qu'elle
est implémentée depuis le bridge v3.1, et les écarts restants vis-à-vis d'une intégration
MOSIP complète.

## 1. Les trois identifiants et leur rôle

**IUN (UIN MOSIP) — identifiant pivot, STRICTEMENT INTERNE.** Dix chiffres nus avec somme de
contrôle Verhoeff, tirés du générateur officiel du kernel MOSIP (`idgenerator`, service
iun-uin-service). Conformément à la norme MOSIP, l'UIN n'est jamais imprimé ni affiché : il
est conservé brut (sans préfixe ni séparateur) dans le dossier d'état civil (identifiants
FHIR `iun.sn/specs/id/uin` et `BIRTH_CONFIGURABLE_IDENTIFIER_2`) et sert de clé de
rapprochement avec les systèmes tiers (CMU, futur ID-Repository MOSIP). L'ancien format
d'affichage « SN-XXXX-XXXX-XX » est abandonné : il introduisait un risque d'échec de
rapprochement et exposait publiquement l'identifiant pivot.

**VID — identifiant virtuel, SEUL AFFICHÉ.** Seize chiffres avec somme de contrôle Verhoeff,
générés selon les règles MOSIP (premier chiffre ≥ 2, pas de séquences ascendantes/descendantes,
pas de triples répétitions). C'est lui qui figure sur les extraits d'acte (naissance et décès,
libellé « IDENTIFIANT VIRTUEL (VID) », groupé XXXX-XXXX-XXXX-XXXX), sur le registre /records
et sur les certificats du bridge. Le mapping UIN↔VID est conservé (`db.iun_vid_map`) en vue de
sa reprise par le service VID de l'ID-Repository lors de l'intégration MOSIP complète. Le VID
émis au pilote est de sémantique « perpétuelle » ; la révocation/rotation de VID relèvera de
l'ID-Repository sur la cible. En l'absence de service `vidgenerator` sur la plateforme MOSIP
actuelle, la génération est locale au bridge mais applique le même algorithme (vérifié par
vecteurs de test ; endpoint de contrôle `/vid-preview`).

**BRN / DRN — numéro d'acte d'état civil (hors périmètre MOSIP).** Format
`RRR-AAAA-NNNNNN` (naissances) et `RRR-AAAA-DNNNNNN` (décès) : code région, année, séquence
atomique par région et par année, à l'image de la numérotation traditionnelle des registres
sénégalais. Il est le numéro d'enregistrement OFFICIEL du dossier OpenCRVS (mutation
`confirmRegistration` au moment de l'enregistrement — plus de numéro interne opaque). Point
d'attention à arbitrer avec le métier avant la production : OpenCRVS recommande des numéros
non séquentiels (les séquences révèlent les volumes d'activité et sont prédictibles) ; le
choix séquentiel est assumé pour la lisibilité registre, la volumétrie du pilote étant faible.

## 2. Flux d'attribution (bridge v3.1)

À l'enregistrement d'une naissance, OpenCRVS appelle de manière synchrone le point
d'extension `/event-registration`, routé vers le bridge IUN. Celui-ci tire l'UIN (kernel
MOSIP), génère le VID, réserve le BRN régional, puis confirme l'enregistrement auprès du
gateway avec le BRN comme numéro officiel, le VID comme identifiant affiché et l'UIN brut en
identifiant interne. L'opération est idempotente (un retry du core réutilise les mêmes
numéros) et tout échec bloque proprement l'enregistrement avec un motif explicite (retenté par
le core). Les décès reçoivent leur DRN en synchrone ; l'UIN/VID du défunt est complété par le
réconciliateur (~30 s). Le QR imprimé sur les actes pointe vers l'URL de vérification du
dossier et n'expose aucun identifiant.

## 3. Écarts restants vis-à-vis de MOSIP (chantier « intégration ID-Repo »)

Le numéro UIN est conforme, mais **l'identité n'existe pas encore dans MOSIP** : aucun packet
n'est soumis au reg-proc, donc pas de dédoublonnage, pas de statut de l'UIN dans
l'ID-Repository, pas de VID géré par la plateforme, pas d'authentification (ID Auth) ni de
credentials. La cible recommandée : à l'enregistrement de naissance, constituer un packet
démographique (nouveau-né, rattachement aux parents) soumis à l'ID-Repository, qui devient
propriétaire du cycle de vie UIN/VID ; le bridge bascule alors de « générateur » à
« orchestrateur ». Ce chantier dépend de la disponibilité des modules reg-proc/idrepo sur la
plateforme MOSIP cible et sera spécifié séparément.

## 4. Décisions demandées

1. Valider la politique d'affichage « UIN interne / VID public » (appliquée au pilote).
2. Arbitrer le format BRN/DRN séquentiel régional vs recommandation OpenCRVS non séquentielle.
3. Prioriser le chantier d'intégration ID-Repository MOSIP dans la feuille de route cible.
4. Valider la purge des données de test du pilote (dossiers pré-v3 portant les anciens formats).
