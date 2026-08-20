"""v2.1 : reprise d'un UIN et d'un VID deja distribues, puis resolution."""
import httpx

B = "http://127.0.0.1:8099"
ok = fails = 0


def check(label, cond, detail=""):
    global ok, fails
    if cond:
        ok += 1
        print("  OK    %s %s" % (label, detail))
    else:
        fails += 1
        print("  ECHEC %s %s" % (label, detail))


c = httpx.Client(timeout=20)

# un UIN reel du cluster (Verhoeff valide) + son VID imprime
UIN = "7613442507"
VID = "3743905140014411"

print("\n1. Reprise d'un UIN distribue avant le registre")
r = c.post(B + "/v1/admin/uin/import", json={"uin": UIN, "note": "reprise Hearth"})
check("import 201", r.status_code == 201, r.status_code)
check("pas un rejeu", r.json()["replayed"] is False)
r2 = c.post(B + "/v1/admin/uin/import", json={"uin": UIN})
check("second import = rejeu", r2.json()["replayed"] is True)
bad = c.post(B + "/v1/admin/uin/import", json={"uin": "1234567891"})  # cle Verhoeff fausse
check("Verhoeff invalide refuse", bad.status_code == 400, bad.status_code)

print("\n2. Identite du defunt")
r = c.post(B + "/idrepository/v1/identity", json={
    "uin": UIN,
    "identity": {"fullName": "baye cheikh", "dateOfBirth": "1984-12-03", "gender": "male"},
    "registrationId": "DKR-2026-D000004"})
check("identite creee", r.status_code == 201, r.status_code)

print("\n3. Reprise du VID deja imprime sur l'acte")
r = c.post(B + "/v1/admin/vid/import", json={"vid": VID, "uin": UIN})
check("import 201", r.status_code == 201, r.status_code)
check("VID conserve tel quel", r.json()["vid"] == VID, r.json()["vid"])
r2 = c.post(B + "/v1/admin/vid/import", json={"vid": VID, "uin": UIN})
check("second import = rejeu", r2.json()["replayed"] is True)
r3 = c.post(B + "/v1/admin/vid/import", json={"vid": VID, "uin": "2105945270"})
check("conflit d'UIN refuse", r3.status_code in (400, 409), r3.status_code)

print("\n4. Le VID repris resout bien")
r = c.get(B + "/v1/vidgenerator/vid/" + VID)
check("resolution 200", r.status_code == 200, r.status_code)
check("pointe le bon UIN", r.json()["uin"] == UIN)
s = c.post(B + "/idrepository/v1/identity/search", json={"vid": VID}).json()
check("recherche par VID", s["verdict"] == "RESOLVED" and s["candidates"][0]["uin"] == UIN)

print("\n5. Desactivation du defunt : le VID repris est revoque")
r = c.patch(B + "/idrepository/v1/identity/uin/" + UIN,
            json={"status": "DEACTIVATED", "reason": "deces DKR-2026-D000004"})
check("desactivee", r.json()["status"] == "DEACTIVATED")
check("VID revoque", c.get(B + "/v1/vidgenerator/vid/" + VID).status_code == 410)
s = c.post(B + "/idrepository/v1/identity/search", json={"vid": VID}).json()
check("le VID revoque ne resout plus", s["verdict"] == "NOT_FOUND", s["verdict"])
s = c.post(B + "/idrepository/v1/identity/search",
           json={"fullName": "baye cheikh", "dateOfBirth": "1984-12-03"}).json()
check("mais le sujet reste retrouvable par demographie",
      s["verdict"] == "RESOLVED_DEMOGRAPHIC" and s["candidates"][0]["status"] == "DEACTIVATED",
      "%s / %s" % (s["verdict"], s["candidates"][0]["status"] if s["candidates"] else "-"))

print("\n6. Import possible meme pour un sujet deja decede")
r = c.post(B + "/v1/admin/vid/import",
           json={"vid": "4296322944600893", "uin": UIN, "status": "REVOKED",
                 "reason": "VID historique"})
check("import historique accepte", r.status_code == 201, r.status_code)
check("statut REVOKED conserve", r.json()["status"] == "REVOKED")

print("\n=== %d OK / %d ECHEC ===" % (ok, fails))
raise SystemExit(1 if fails else 0)
