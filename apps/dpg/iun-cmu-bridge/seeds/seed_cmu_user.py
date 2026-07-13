# -*- coding: utf-8 -*-
# Cree un InteractiveUser dedie pour le bridge (tokenAuth GraphQL resout un InteractiveUser,
# pas un TechnicalUser). Idempotent. set_password() = hash legacy OpenIMIS correct.
from __future__ import print_function
import datetime as dt, traceback
NOW = dt.datetime(2026, 1, 1)
LOGIN = "iun-cmu-bridge"
PWD = "IunCmuBridge2026!"
out = {}
try:
    from core.models import InteractiveUser, User, UserRole
    # role : reprendre celui de Admin (IMIS Administrator) pour avoir les droits insuree/policy
    role_id = None
    admin = InteractiveUser.objects.filter(login_name="Admin", validity_to__isnull=True).first()
    if admin:
        ur = UserRole.objects.filter(user=admin, validity_to__isnull=True).order_by("-id").first()
        role_id = getattr(ur, "role_id", None)
    out["admin_role_id"] = role_id

    iu = InteractiveUser.objects.filter(login_name=LOGIN, validity_to__isnull=True).first()
    if not iu:
        iu = InteractiveUser(login_name=LOGIN, last_name="IUN", other_names="CMU Bridge",
                             language_id="en", audit_user_id=1, is_associated=False)
        iu.set_password(PWD)
        iu.save()
        out["interactive_user"] = "CREATED id=%s" % iu.id
    else:
        iu.set_password(PWD); iu.save()
        out["interactive_user"] = "EXISTS id=%s (pwd reset)" % iu.id

    if role_id and not UserRole.objects.filter(user=iu, role_id=role_id, validity_to__isnull=True).exists():
        UserRole.objects.create(user=iu, role_id=role_id, audit_user_id=1, validity_from=NOW)
        out["user_role"] = "linked role %s" % role_id
    else:
        out["user_role"] = "role already linked or none"

    # core_User wrapper : reprendre l'existant (cree avec le TechnicalUser) et le pointer sur l'InteractiveUser
    cu = User.objects.filter(username=LOGIN).first()
    if not cu:
        cu = User(username=LOGIN)
    cu.i_user = iu
    try:
        cu.t_user = None
    except Exception:
        pass
    cu.save()
    out["core_user"] = "id=%s -> i_user=%s" % (cu.id, iu.id)
except Exception as e:  # noqa
    out["error"] = str(e)
    traceback.print_exc()

print("==== SEED CMU USER RESULTS ====")
for k, v in out.items():
    print("  %-16s : %s" % (k, v))
