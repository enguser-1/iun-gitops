{{/*
Expand the name of the chart.
*/}}
{{- define "iun-uin-bridge.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name.
*/}}
{{- define "iun-uin-bridge.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Chart name and version as used by the chart label.
*/}}
{{- define "iun-uin-bridge.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels.
*/}}
{{- define "iun-uin-bridge.labels" -}}
helm.sh/chart: {{ include "iun-uin-bridge.chart" . }}
{{ include "iun-uin-bridge.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: iun-platform
iun.sn/dpg: uin-custom
{{- end -}}

{{/*
Selector labels — utilises pour Service selector et Deployment matchLabels.
*/}}
{{- define "iun-uin-bridge.selectorLabels" -}}
app.kubernetes.io/name: {{ include "iun-uin-bridge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Service account name.
*/}}
{{- define "iun-uin-bridge.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{ default (include "iun-uin-bridge.fullname" .) .Values.serviceAccount.name }}
{{- else -}}
{{ default "default" .Values.serviceAccount.name }}
{{- end -}}
{{- end -}}

{{/*
Image reference — utilise digest si present (immuable), sinon tag.
*/}}
{{- define "iun-uin-bridge.image" -}}
{{- $registry := .Values.image.registry -}}
{{- $ns := .Values.image.namespace -}}
{{- $repo := .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{- printf "%s/%s/%s@%s" $registry $ns $repo .Values.image.digest -}}
{{- else -}}
{{- printf "%s/%s/%s:%s" $registry $ns $repo .Values.image.tag -}}
{{- end -}}
{{- end -}}
