# ============================================================================
# IUN UIN Service - production image
# ============================================================================
# Base = OCP internal mirror python 3.11 UBI9 (pas de pull WAN, pas de
# rate limit Docker Hub). Compatible SCC restricted-v2 OpenShift par defaut
# (USER 1001 mosip standard, group root mapping).
#
# Note Python 3.11 vs 3.12 : notre code utilise tuple[X,Y] (3.9+) + asyncpg
# + fastapi. Aucune dependance 3.12-only.
# ============================================================================
FROM image-registry.openshift-image-registry.svc:5000/openshift/python:3.11-ubi9

# WORKDIR par defaut de l'image OCP python : /opt/app-root/src
# USER par defaut : 1001 (non-root, OCP-friendly)

# requirements en premier pour cache Docker layer
COPY --chown=1001:0 requirements.txt ./

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Code applicatif
COPY --chown=1001:0 app/ ./app/

EXPOSE 8080

# Healthcheck via Python stdlib (pas besoin de curl)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/actuator/health').getcode()==200 else 1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2", "--log-level", "info"]
