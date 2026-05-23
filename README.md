# IUN GitOps — Socle plateforme OpenShift 4.16

Repository GitOps pour le programme **IUN Sénégal**. Géré par **Argo CD** (OpenShift GitOps Operator) avec le pattern **app-of-apps** et **Kustomize** pour les overlays par environnement.

Cible : OpenShift Container Platform 4.16, déploiement déclaratif des 11 Operators socle identifiés au §4 du rapport `EVALUATION_OPENSHIFT_v4.16.md`.

---

## 0. Architecture : instance Argo CD dédiée IUN (Option B)

**Décision** : le programme IUN provisionne sa **propre instance Argo CD** dans le namespace `iun-gitops`, **distincte** de l'instance partagée par défaut (`openshift-gitops`) déployée par l'OpenShift GitOps Operator cluster-wide.

```
                +-----------------------------------------------+
                |  Cluster OCP 4.16 (api.origins.heritage.africa)|
                +-----------------------------------------------+
                |                                               |
                |  openshift-operators                          |
                |    +- openshift-gitops-operator (cluster-wide)|
                |         (déjà installé, non géré par ce repo) |
                |                                               |
                |  openshift-gitops   (instance par défaut —    |
                |    +- argocd        plateforme partagée OCP,  |
                |                     hors scope IUN)           |
                |                                               |
                |  iun-gitops         (instance dédiée IUN)     |
                |    +- iun-argocd  <-- ce repo                 |
                |         +- AppProject iun-platform            |
                |         +- Root Application iun-root-dev      |
                |         +- 11 Applications socle              |
                |                                               |
                +-----------------------------------------------+
```

### Pourquoi une instance dédiée ?

- **Isolation RBAC** : les administrateurs IUN n'ont pas à toucher à l'instance Argo CD cluster (utilisée par d'autres équipes / SRE OCP).
- **Cycle de vie indépendant** : on peut mettre à jour la version Argo CD, ses plugins, ses notifications, sans coordination cross-équipes.
- **Périmètre clair** : un seul `AppProject` (`iun-platform`), un seul Git source repo, un namespace d'admin (`iun-gitops`) — tout est traçable côté audit.
- **Souveraineté** : facilite la migration future vers un Git on-prem (GitLab souverain, Gitea) sans impacter d'autres équipes.

### Pourquoi pas l'instance partagée (Option A — rejetée) ?

- Risque de collision de noms / projects avec d'autres équipes.
- RBAC commun à gérer transversalement (lent).
- Couplage upgrade : impossible de figer une version Argo CD pour stabilité IUN sans bloquer les autres.

---

## 1. Arborescence

```
gitops/
├── bootstrap/
│   ├── 00-iun-gitops-namespace.yaml    Namespace iun-gitops
│   ├── 01-iun-argocd.yaml              CR ArgoCD iun-argocd (instance dédiée)
│   ├── 02-iun-rbac.yaml                ClusterRoleBindings pour le SA controller
│   ├── 03-root-app-project.yaml        AppProject iun-platform
│   ├── 04-root-application.yaml        Root Application (app-of-apps)
│   └── kustomization.yaml
│
├── apps/socle/                Les 11 Operators socle — toutes dans ns iun-gitops
│   ├── 01-openshift-gitops.yaml … 11-rhacs.yaml
│   └── kustomization.yaml
│
├── components/<op>/base/      Subscription + OperatorGroup + Namespace par Operator
│
├── environments/{dev,staging,prod}/kustomization.yaml
│
├── .gitignore
└── README.md
```

### Pattern de déploiement

```
   bootstrap/04-root-application.yaml  (Root Application "iun-root-dev")
              |   namespace: iun-gitops   (instance dédiée)
              v   source.path = environments/dev
   environments/dev/kustomization.yaml
              |
              v   resources += ../../apps/socle
   apps/socle/*.yaml              (11 Applications, toutes dans ns iun-gitops)
              |
              v   chacune source.path = components/<op>/base
   components/<op>/base/*.yaml    (Subscription + OperatorGroup + Namespace)
```

---

## 2. Operators socle

| #  | Operator                          | Namespace cible            | Channel        | Source              |
|----|-----------------------------------|----------------------------|----------------|---------------------|
| 1  | OpenShift GitOps (Argo CD)        | openshift-operators        | latest         | redhat-operators    |
| 2  | OpenShift Pipelines (Tekton)      | openshift-operators        | latest         | redhat-operators    |
| 3  | OpenShift Service Mesh 3          | openshift-operators        | stable         | redhat-operators    |
| 4  | Red Hat Build of Keycloak         | rhbk-operator              | stable-v26     | redhat-operators    |
| 5  | CloudNativePG                     | cnpg-system                | stable-v1.24   | community-operators |
| 6  | AMQ Streams (Kafka)               | amq-streams                | stable         | redhat-operators    |
| 7  | OpenShift Logging (Loki + Vector) | openshift-logging          | stable-6.0     | redhat-operators    |
| 8  | OpenShift Data Foundation         | openshift-storage          | stable-4.16    | redhat-operators    |
| 9  | cert-manager                      | cert-manager-operator      | stable-v1      | redhat-operators    |
| 10 | External Secrets Operator         | external-secrets-operator  | stable         | community-operators |
| 11 | Red Hat Advanced Cluster Security | rhacs-operator             | stable         | redhat-operators    |

> Toutes les **Applications Argo CD** (`apps/socle/*.yaml`) vivent dans `iun-gitops`. Leurs `destination.namespace` ci-dessus restent les namespaces OLM cibles de chaque Operator.

---

## 3. Commandes PowerShell

> **Pré-requis Windows** : `git`, `oc` 4.16+, kubeconfig pointant sur `https://api.origins.heritage.africa:6443`.
>
> **⚠ Certificat API expiré sur ce cluster.** Toutes les commandes `oc` ci-dessous incluent `--insecure-skip-tls-verify=true`. Alternative : faire `oc login --insecure-skip-tls-verify=true …` une fois — la session courante mémorise le flag (`~/.kube/config`). Renouvellement du cert API tracé en §5 "Hardening post-PoC".

### (a) Préchecks — Operator déjà installé

```powershell
$ApiUrl    = "https://api.origins.heritage.africa:6443"
$TlsBypass = "--insecure-skip-tls-verify=true"

# Login (le flag est mémorisé pour la session courante)
oc login $ApiUrl $TlsBypass -u <user>

# 1. Confirmer que l'OpenShift GitOps Operator est déjà installé cluster-wide
oc get csv -A $TlsBypass | Select-String "openshift-gitops-operator"
oc get crd argocds.argoproj.io $TlsBypass
oc get crd applications.argoproj.io $TlsBypass

# 2. Vérifier qu'aucune instance ArgoCD `iun-argocd` n'existe déjà
oc get argocd -A $TlsBypass | Select-String "iun-argocd"
# (résultat attendu : aucune ligne)
```

### (b) Bootstrap de l'instance Argo CD IUN

```powershell
Set-Location C:\IUN_APP\gitops

# Appliquer le bundle bootstrap (Namespace + ArgoCD CR + RBAC + AppProject + Root App)
oc apply -k bootstrap/ $TlsBypass

# Attendre que l'Operator ait provisionné l'instance
oc wait --for=jsonpath='{.status.phase}'=Available `
  argocd/iun-argocd -n iun-gitops --timeout=300s $TlsBypass

# Attendre les deployments clés
oc wait --for=condition=Available --timeout=300s `
  deployment/iun-argocd-server -n iun-gitops $TlsBypass
oc wait --for=condition=Available --timeout=300s `
  deployment/iun-argocd-repo-server -n iun-gitops $TlsBypass
```

### (c) Récupérer URL UI + mot de passe admin

```powershell
$ArgoUrl = oc get route iun-argocd-server -n iun-gitops $TlsBypass `
  -o jsonpath='{"https://"}{.spec.host}'
Write-Host "Argo CD UI: $ArgoUrl"

$ArgoPwd = oc get secret iun-argocd-cluster -n iun-gitops $TlsBypass `
  -o jsonpath='{.data.admin\.password}' |
  ForEach-Object {
    [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($_))
  }
Write-Host "Argo CD admin password: $ArgoPwd"
```

### (d) Vérifier la synchronisation

```powershell
# Liste des Applications et leur état (dans iun-gitops)
oc get applications -n iun-gitops $TlsBypass

# Détail d'une Application
oc describe application socle-openshift-servicemesh -n iun-gitops $TlsBypass

# État des Subscriptions OLM
oc get subscriptions -A $TlsBypass

# État de l'install : CSV doit passer en "Succeeded"
oc get csv -A $TlsBypass | Select-String -NotMatch "Succeeded"

# Forcer un refresh / sync manuel
oc annotate application socle-cloudnative-pg -n iun-gitops `
  argocd.argoproj.io/refresh=hard --overwrite $TlsBypass

# Vue d'ensemble santé
oc get applications -n iun-gitops $TlsBypass `
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
```

### (e) Promouvoir vers staging / prod

```powershell
Copy-Item C:\IUN_APP\gitops\bootstrap\04-root-application.yaml `
          C:\IUN_APP\gitops\bootstrap\04-root-application-prod.yaml
# Éditer : metadata.name -> iun-root-prod, source.path -> environments/prod
oc apply -f C:\IUN_APP\gitops\bootstrap\04-root-application-prod.yaml $TlsBypass
```

### (f) Rollback complet de l'instance IUN

```powershell
oc delete application iun-root-dev -n iun-gitops $TlsBypass
oc delete argocd iun-argocd       -n iun-gitops $TlsBypass
oc delete namespace iun-gitops    $TlsBypass
```

---

## 4. Conventions

- **Une Subscription par fichier** dans `components/<op>/base/subscription.yaml`.
- **Un seul AppProject** (`iun-platform`) à ce stade ; segmenter par BU quand l'équipe scalera.
- **Pas de Secrets en clair dans Git** : tout passe par External Secrets Operator + Vault, ou Sealed Secrets en transition.
- **`installPlanApproval: Automatic`** en dev/staging, à basculer en `Manual` pour prod.
- **Toutes les Applications Argo CD** vivent dans `iun-gitops` (`metadata.namespace`). Leurs `destination.namespace` restent les namespaces OLM cibles.

---

## 5. Hardening post-PoC (dette technique connue)

À traiter après validation fonctionnelle du PoC, avant bascule staging/prod :

| # | Item                                  | Description                                                                                                                                            | Priorité |
|---|---------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------|----------|
| 1 | Cert API serveur OCP expiré           | Renouveler le cert de `api.origins.heritage.africa:6443` pour supprimer `--insecure-skip-tls-verify=true`. Procédure `oc adm` côté SRE.                | Haute    |
| 2 | SA controller = cluster-admin         | Remplacer le CRB cluster-admin (`bootstrap/02-iun-rbac.yaml`) par un ClusterRole fine-grained `iun-argocd-platform` (operators.coreos.com, rbac, …).   | Haute    |
| 3 | SSO Argo CD désactivé                 | Activer OIDC sur Keycloak (RHBK) une fois Keycloak provisionné ; retirer le rôle local admin mappé aux `system:cluster-admins`.                        | Moyenne  |
| 4 | HA Argo CD désactivée                 | `spec.ha.enabled: true` sur le CR `iun-argocd` (Redis cluster, controller sharding). Coût ~3x mémoire.                                                | Moyenne  |
| 5 | sourceRepos restreint                 | Déjà restreint à `https://github.com/enguser-1/iun-gitops*`. À durcir encore après migration vers Git on-prem.                                         | Basse    |
| 6 | installPlanApproval Automatic         | Basculer en `Manual` pour prod (patch overlay `environments/prod/`).                                                                                  | Basse    |
| 7 | Pipeline de validation Kustomize      | Tekton job `kustomize build environments/dev \| oc apply --dry-run=server -f -` sur chaque PR.                                                         | Basse    |

---

## 6. Phase 2 (hors scope)

- Compléter `apps/iun-api/` avec l'Application déployant l'API .NET conteneurisée.
- Ajouter les CR métier (Cluster CNPG, Kafka, Keycloak, StorageCluster) une fois les Operators Succeeded.
- Operators phase 2 : Cluster Observability, OpenTelemetry, Tempo, Compliance, OADP, KEDA, Serverless.
- Sync-waves Argo CD (annotation `argocd.argoproj.io/sync-wave`) pour ordonner Operators -> CRs -> applicatifs.

---

## 7. Références

- `..\EVALUATION_OPENSHIFT_v4.16.md` — §4 liste exhaustive des Operators recommandés.
- `..\SYNTHESE_ET_PLAN_DEMARRAGE_v1.md` — feuille de route programme.
- [Argo CD app-of-apps pattern](https://argo-cd.readthedocs.io/en/stable/operator-manual/cluster-bootstrapping/)
- [OpenShift GitOps — Multiple Argo CD instances](https://docs.openshift.com/gitops/latest/argocd_instance/setting-up-argocd-instance.html)
- [ArgoCD CRD (argoproj.io/v1beta1)](https://argocd-operator.readthedocs.io/en/latest/reference/argocd/)
