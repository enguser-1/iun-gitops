# IUN GitOps — Socle plateforme OpenShift 4.16

Repository GitOps pour le programme **IUN Sénégal**. Géré par **Argo CD** (OpenShift GitOps Operator) avec le pattern **app-of-apps** et **Kustomize** pour les overlays par environnement.

Cible : OpenShift Container Platform 4.16, déploiement déclaratif des 11 Operators socle identifiés au §4 du rapport `EVALUATION_OPENSHIFT_v4.16.md`.

---

## 1. Arborescence

```
gitops/
├── bootstrap/                  Manifests d'amorçage (à appliquer une seule fois, hors Argo CD)
│   ├── 01-openshift-gitops-subscription.yaml   Installe l'Operator Argo CD
│   ├── 02-root-app-project.yaml                AppProject "iun-platform"
│   ├── 03-root-application.yaml                Root Application (app-of-apps)
│   └── kustomization.yaml
│
├── apps/                       Applications Argo CD (un fichier = un chantier)
│   ├── socle/                  Les 11 Operators socle
│   │   ├── 01-openshift-gitops.yaml
│   │   ├── 02-openshift-pipelines.yaml
│   │   ├── 03-openshift-servicemesh.yaml
│   │   ├── 04-rhbk.yaml                        (Red Hat Build of Keycloak)
│   │   ├── 05-cloudnative-pg.yaml
│   │   ├── 06-amq-streams.yaml
│   │   ├── 07-openshift-logging.yaml
│   │   ├── 08-openshift-data-foundation.yaml
│   │   ├── 09-cert-manager.yaml
│   │   ├── 10-external-secrets.yaml
│   │   ├── 11-rhacs.yaml
│   │   └── kustomization.yaml
│   └── iun-api/                Applicatif métier IUN (placeholder phase 2)
│       └── README.md
│
├── components/                 Bases Kustomize réutilisables (un dossier = un Operator)
│   ├── openshift-gitops/base/
│   ├── openshift-pipelines/base/
│   ├── openshift-servicemesh/base/
│   ├── rhbk/base/
│   ├── cloudnative-pg/base/
│   ├── amq-streams/base/
│   ├── openshift-logging/base/
│   ├── openshift-data-foundation/base/
│   ├── cert-manager/base/
│   ├── external-secrets/base/
│   └── rhacs/base/
│       Chaque base contient typiquement :
│         - namespace.yaml         (si namespace dédié)
│         - operatorgroup.yaml     (si namespace dédié)
│         - subscription.yaml      (Subscription OLM)
│         - kustomization.yaml
│
├── environments/               Overlays par environnement (Argo CD pointe sur ces dossiers)
│   ├── dev/kustomization.yaml
│   ├── staging/kustomization.yaml
│   └── prod/kustomization.yaml
│
├── .gitignore
└── README.md
```

### Pattern de déploiement

```
   Root Application (bootstrap/03-root-application.yaml)
              │
              ▼  source.path = environments/dev
   environments/dev/kustomization.yaml
              │
              ▼  resources += ../../apps/socle
   apps/socle/*.yaml              (11 Applications)
              │
              ▼  chacune source.path = components/<op>/base
   components/<op>/base/*.yaml    (Subscription + OperatorGroup + Namespace)
```

---

## 2. Operators socle (les 11 du §4 du rapport)

| # | Operator                              | Namespace                  | Channel        | Source             |
|---|---------------------------------------|----------------------------|----------------|--------------------|
| 1 | OpenShift GitOps (Argo CD)            | `openshift-operators`      | `latest`       | redhat-operators   |
| 2 | OpenShift Pipelines (Tekton)          | `openshift-operators`      | `latest`       | redhat-operators   |
| 3 | OpenShift Service Mesh 3              | `openshift-operators`      | `stable`       | redhat-operators   |
| 4 | Red Hat Build of Keycloak             | `rhbk-operator`            | `stable-v26`   | redhat-operators   |
| 5 | CloudNativePG                         | `cnpg-system`              | `stable-v1.24` | community-operators|
| 6 | AMQ Streams (Kafka)                   | `amq-streams`              | `stable`       | redhat-operators   |
| 7 | OpenShift Logging (Loki + Vector)     | `openshift-logging` + `openshift-operators-redhat` | `stable-6.0` | redhat-operators |
| 8 | OpenShift Data Foundation             | `openshift-storage`        | `stable-4.16`  | redhat-operators   |
| 9 | cert-manager                          | `cert-manager-operator`    | `stable-v1`    | redhat-operators   |
| 10| External Secrets Operator             | `external-secrets-operator`| `stable`       | community-operators|
| 11| Red Hat Advanced Cluster Security     | `rhacs-operator`           | `stable`       | redhat-operators   |

> Les `channel` sont indicatifs et à vérifier au moment du déploiement via `oc get packagemanifest <name> -n openshift-marketplace -o jsonpath='{.status.channels[*].name}'`.

---

## 3. Commandes PowerShell

> **Pré-requis poste de travail Windows** : `git`, `oc` (OpenShift CLI 4.16+) et un `kubeconfig` valide pointant sur le cluster cible (`$env:KUBECONFIG`).

### (a) Initialiser Git localement

```powershell
Set-Location C:\IUN_APP\gitops
git init -b main
git add .
git commit -m "chore(gitops): scaffold socle plateforme IUN"
```

### (b) Ajouter le remote quand l'URL sera connue

```powershell
# Remplacer <URL_GIT> par l'URL effective (GitLab on-prem, Bitbucket, Gitea souverain, etc.)
$RepoUrl = "https://git.example.sn/iun/gitops.git"

git remote add origin $RepoUrl
git push -u origin main

# Mettre à jour tous les repoURL dans les manifests (replace en bloc)
Get-ChildItem -Path C:\IUN_APP\gitops -Recurse -Filter *.yaml |
  ForEach-Object {
    (Get-Content $_.FullName -Raw) `
      -replace 'https://CHANGE-ME\.example\.com/iun/gitops\.git', $RepoUrl |
      Set-Content -NoNewline -Encoding UTF8 $_.FullName
  }

git add .
git commit -m "chore(gitops): set real repoURL"
git push
```

### (c) Bootstrap Argo CD sur le cluster

```powershell
# Se connecter au cluster (adapter URL et credentials)
oc login https://api.cluster.example.sn:6443

# 1) Installer l'Operator OpenShift GitOps
oc apply -f C:\IUN_APP\gitops\bootstrap\01-openshift-gitops-subscription.yaml

# 2) Attendre que le CRD Application soit prêt (peut prendre 2-3 min)
oc wait --for=condition=Established crd/applications.argoproj.io --timeout=300s

# 3) Attendre que le namespace openshift-gitops et l'instance ArgoCD par défaut soient prêts
oc wait --for=condition=Available --timeout=300s `
  deployment/openshift-gitops-server -n openshift-gitops

# 4) Créer l'AppProject puis la Root Application (app-of-apps)
oc apply -f C:\IUN_APP\gitops\bootstrap\02-root-app-project.yaml
oc apply -f C:\IUN_APP\gitops\bootstrap\03-root-application.yaml

# (Astuce : récupérer le mot de passe admin Argo CD)
$ArgoPwd = oc get secret openshift-gitops-cluster -n openshift-gitops `
  -o jsonpath='{.data.admin\.password}' | ForEach-Object { `
    [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($_)) }
Write-Host "Argo CD admin password: $ArgoPwd"

# (Astuce : récupérer la Route)
oc get route openshift-gitops-server -n openshift-gitops `
  -o jsonpath='{"https://"}{.spec.host}{"\n"}'
```

### (d) Vérifier la synchronisation

```powershell
# Liste des Applications et leur état
oc get applications -n openshift-gitops

# Détail d'une Application (ici le socle Service Mesh)
oc describe application socle-openshift-servicemesh -n openshift-gitops

# État des Subscriptions OLM (toutes les installations d'Operators)
oc get subscriptions -A

# État de l'install : CSV (ClusterServiceVersion) doit passer en "Succeeded"
oc get csv -A | Select-String -NotMatch "Succeeded"

# Forcer un refresh / sync manuel d'une Application
oc annotate application socle-cloudnative-pg -n openshift-gitops `
  argocd.argoproj.io/refresh=hard --overwrite

# Vue d'ensemble santé : doit afficher Healthy / Synced pour les 11
oc get applications -n openshift-gitops `
  -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
```

### (e) Promouvoir vers staging / prod

```powershell
# Dupliquer la Root Application en pointant sur l'overlay cible
Copy-Item C:\IUN_APP\gitops\bootstrap\03-root-application.yaml `
          C:\IUN_APP\gitops\bootstrap\03-root-application-prod.yaml

# Éditer le fichier : metadata.name -> iun-root-prod, source.path -> environments/prod
# Puis :
oc apply -f C:\IUN_APP\gitops\bootstrap\03-root-application-prod.yaml
```

---

## 4. Conventions

- **Une Subscription par fichier** dans `components/<op>/base/subscription.yaml`.
- **Un seul AppProject** (`iun-platform`) à ce stade ; segmenter par BU quand l'équipe scalera.
- **Pas de Secrets en clair dans Git** : tout passe par External Secrets Operator + Vault, ou Sealed Secrets en transition.
- **`installPlanApproval: Automatic`** en dev/staging, à basculer en `Manual` pour prod (cf. patch d'exemple dans `environments/prod/kustomization.yaml`).
- **Les `repoURL: https://CHANGE-ME.example.com/iun/gitops.git`** sont des placeholders ; ils doivent être remplacés par l'URL Git réelle avant le premier `oc apply` (cf. §3.b).

---

## 5. Phase 2 (hors scope de ce scaffold)

- Compléter `apps/iun-api/` avec l'Application déployant l'API .NET conteneurisée.
- Ajouter les `Cluster`, `Kafka`, `Keycloak`, `StorageCluster` (CR des Operators) une fois les Operators `Succeeded`.
- Ajouter Operators phase 2 du rapport §4 : Cluster Observability, OpenTelemetry, Tempo, Compliance, OADP, KEDA, Serverless.
- Implémenter sync-waves Argo CD (annotation `argocd.argoproj.io/sync-wave`) pour ordonner Operators → CRs → applicatifs.
- Ajouter un pipeline Tekton de validation Kustomize (`kustomize build environments/dev | oc apply --dry-run=server -f -`).

---

## 6. Références

- `..\EVALUATION_OPENSHIFT_v4.16.md` — §4 liste exhaustive des Operators recommandés.
- `..\SYNTHESE_ET_PLAN_DEMARRAGE_v1.md` — feuille de route programme.
- [Argo CD app-of-apps pattern](https://argo-cd.readthedocs.io/en/stable/operator-manual/cluster-bootstrapping/)
- [OpenShift GitOps documentation](https://docs.openshift.com/gitops/)
