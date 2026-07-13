# -*- coding: utf-8 -*-
# Seeder dataset CMU v1 (officiel) pour OpenIMIS - IUN Senegal.
# A executer DANS le pod backend :  python manage.py shell -c "exec(open('/tmp/seed_cmu_v1.py').read())"
# Idempotent. Cree : (1) produit CMU-ENF gratuite 0-5 ans premium 0, national ;
#                    (2) officer d'enrolement IUN-AUTO ; (3) technical user pour le bridge.
# NB schemas OpenIMIS intriques -> on itere sur les erreurs de modele live.
from __future__ import print_function
import datetime as dt
import traceback

NOW = dt.datetime(2026, 1, 1)
END = dt.datetime(2035, 12, 31)
ADMIN_AUDIT = 1
RESULTS = {}

# --- 1) Produit CMU-ENF (gratuite enfants 0-5 ans, premium 0, national) -------
try:
    from product.models import Product
    prod = Product.objects.filter(code="CMUENF", validity_to__isnull=True).first()
    if not prod:
        prod = Product.objects.create(
            code="CMUENF",
            name="CMU - Gratuite Enfants 0-5 ans",
            location=None,                       # national
            date_from=NOW, date_to=END,
            insurance_period=12,                 # mois (police annuelle renouvelable)
            lump_sum=0, max_members=12,
            premium_adult=0, premium_child=0,
            grace_period_enrolment=0,
            registration_lump_sum=0, registration_fee=0,
            general_assembly_lump_sum=0, general_assembly_fee=0,
            audit_user_id=ADMIN_AUDIT,
            validity_from=NOW,
        )
        RESULTS["product"] = "CREATED id=%s code=%s" % (prod.id, prod.code)
    else:
        RESULTS["product"] = "EXISTS id=%s code=%s" % (prod.id, prod.code)
except Exception as e:  # noqa
    RESULTS["product"] = "ERROR: %s" % e
    traceback.print_exc()

# --- 2) Officer d'enrolement IUN-AUTO ----------------------------------------
try:
    from core.models import Officer
    off = Officer.objects.filter(code="IUNAUTO", validity_to__isnull=True).first()
    if not off:
        off = Officer.objects.create(
            code="IUNAUTO",
            last_name="IUN", other_names="Auto-Enrolment",
            dob=dt.date(2000, 1, 1),
            location_id=69,                      # Dakar (region) ; ajuster si besoin
            audit_user_id=ADMIN_AUDIT,
            validity_from=NOW,
        )
        RESULTS["officer"] = "CREATED id=%s code=%s" % (off.id, off.code)
    else:
        RESULTS["officer"] = "EXISTS id=%s code=%s" % (off.id, off.code)
except Exception as e:  # noqa
    RESULTS["officer"] = "ERROR: %s" % e
    traceback.print_exc()

# --- 3) Technical user pour le bridge (acces API) ----------------------------
try:
    from core.models import TechnicalUser
    tu = TechnicalUser.objects.filter(username="iun-cmu-bridge").first()
    if not tu:
        tu = TechnicalUser(username="iun-cmu-bridge", email="cmu-bridge@iun.sn",
                           is_staff=False, is_superuser=True)
        tu.set_password("IunCmuBridge2026!")     # MVP : a deplacer en secret
        tu.save()
        RESULTS["techuser"] = "CREATED iun-cmu-bridge (superuser)"
    else:
        tu.set_password("IunCmuBridge2026!"); tu.is_superuser = True; tu.save()
        RESULTS["techuser"] = "RESET iun-cmu-bridge password"
except Exception as e:  # noqa
    RESULTS["techuser"] = "ERROR: %s" % e
    traceback.print_exc()

print("==== SEED CMU v1 RESULTS ====")
for k, v in RESULTS.items():
    print("  %-9s : %s" % (k, v))
