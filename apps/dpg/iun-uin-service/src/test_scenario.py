"""Scenario de bout en bout du registre souverain, contre un vrai PostgreSQL."""
import json
import httpx

B = "http://127.0.0.1:8099"
ok = fails = 0


def check(label, cond, detail=""):
    global ok, fails
    if cond:
        ok += 1
        print("  OK   %s %s" % (label, detail))
    else:
        fails += 1
        print("  ECHEC %s %s" % (label, detail))


c = httpx.Client(timeout=20)

print("\n1. Compatibilite v3.6 : POST sans corps, champ 'uin' a plat")
r = c.post(B + "/v1/idgenerator/uin")
d = r.json()
check("status 200", r.status_code == 200, r.status_code)
check("uin a plat", isinstance(d.get("uin"), str) and len(d["uin"]) == 10, d.get("uin"))
check("uin sous response", (d.get("response") or {}).get("uin") == d.get("uin"))
check("etat CONFIRMED", d["state"] == "CONFIRMED", d["state"])
UIN_A = d["uin"]

print("\n2. Idempotence : deux chemins, meme cle -> meme numero")
key = "Composition/abc-123"
r1 = c.post(B + "/v1/idgenerator/uin", json={"idempotencyKey": key, "requester": "poller"}).json()
r2 = c.post(B + "/v1/idgenerator/uin", json={"idempotencyKey": key, "requester": "webhook"}).json()
check("meme UIN", r1["uin"] == r2["uin"], "%s == %s" % (r1["uin"], r2["uin"]))
check("second rejoue", r2["replayed"] is True)
check("premier non rejoue", r1["replayed"] is False)

print("\n3. Reservation : un numero frappe non confirme est visible")
r = c.post(B + "/v1/idgenerator/uin", json={"reserve": True, "requester": "bridge"}).json()
check("etat RESERVED", r["state"] == "RESERVED", r["state"])
stale = c.get(B + "/v1/admin/reservations/stale?minutes=0").json()
check("apparait dans stale", any(x["uin"] == r["uin"] for x in stale["reservations"]),
      "%d en attente" % stale["count"])
conf = c.post(B + "/v1/idgenerator/reservation/%s/confirm" % r["reservationId"])
check("confirmation 200", conf.status_code == 200, conf.status_code)
stale2 = c.get(B + "/v1/admin/reservations/stale?minutes=0").json()
check("disparait de stale", not any(x["uin"] == r["uin"] for x in stale2["reservations"]))
check("double confirmation refusee",
      c.post(B + "/v1/idgenerator/reservation/%s/confirm" % r["reservationId"]).status_code == 404)

print("\n4. Identite : creer l'identite derriere le numero")
ident = {
    "fullName": "NDIAYE Awa", "dateOfBirth": "1984-03-12", "gender": "F",
    "placeOfBirth": "Thies", "nationalId": "2116198403310",
    "birthRegistrationNumber": "THS-1984-B004120",
}
r = c.post(B + "/idrepository/v1/identity",
           json={"uin": UIN_A, "identity": ident, "registrationId": "THS-1984-B004120"})
check("creation 201", r.status_code == 201, r.status_code)
check("statut ACTIVE", r.json()["status"] == "ACTIVE")
r = c.post(B + "/idrepository/v1/identity",
           json={"uin": "1234567890", "identity": ident})
check("UIN inconnu refuse", r.status_code == 400, r.status_code)

print("\n5. VID : emis par le registre, donc revocable")
v = c.post(B + "/v1/vidgenerator/vid", json={"uin": UIN_A}).json()
VID_A = v["vid"]
check("16 chiffres", len(VID_A) == 16 and VID_A.isdigit(), VID_A)
check("formate", v["formatted"] == "-".join(VID_A[i:i+4] for i in range(0, 16, 4)), v["formatted"])
v2 = c.post(B + "/v1/vidgenerator/vid", json={"uin": UIN_A}).json()
check("perpetuel rejoue", v2["vid"] == VID_A and v2["replayed"] is True)
v3 = c.post(B + "/v1/vidgenerator/vid", json={"uin": UIN_A, "rotate": True}).json()
check("rotation donne un nouveau VID", v3["vid"] != VID_A, v3["vid"])
old = c.get(B + "/v1/vidgenerator/vid/%s" % VID_A)
check("ancien VID revoque -> 410", old.status_code == 410, old.status_code)
VID_A = v3["vid"]
res = c.get(B + "/v1/vidgenerator/vid/%s" % VID_A).json()
check("VID resout vers l'UIN", res["uin"] == UIN_A)

print("\n6. Resolution : resoudre AVANT de frapper")
s = c.post(B + "/idrepository/v1/identity/search",
           json={"nationalId": "2116198403310"}).json()
check("verdict RESOLVED", s["verdict"] == "RESOLVED", s["verdict"])
check("bon UIN", s["candidates"][0]["uin"] == UIN_A)
check("type NATIONAL_ID", s["candidates"][0]["matchType"] == "NATIONAL_ID")

s = c.post(B + "/idrepository/v1/identity/search",
           json={"fullName": "awa ndiaye", "dateOfBirth": "1984-03-12"}).json()
check("nom inverse + date -> resolu", s["verdict"] == "RESOLVED_DEMOGRAPHIC", s["verdict"])
check("type DEMOGRAPHIC_STRONG", s["candidates"][0]["matchType"] == "DEMOGRAPHIC_STRONG")

s = c.post(B + "/idrepository/v1/identity/search", json={"vid": VID_A}).json()
check("resolution par VID", s["verdict"] == "RESOLVED" and s["candidates"][0]["uin"] == UIN_A)

s = c.post(B + "/idrepository/v1/identity/search",
           json={"fullName": "FALL Ousmane", "dateOfBirth": "1950-01-01"}).json()
check("inconnu -> NOT_FOUND", s["verdict"] == "NOT_FOUND", s["verdict"])

print("\n   ambiguite : deux homonymes memes nom et date")
u2 = c.post(B + "/v1/idgenerator/uin").json()["uin"]
c.post(B + "/idrepository/v1/identity", json={"uin": u2, "identity": {
    "fullName": "NDIAYE Awa", "dateOfBirth": "1984-03-12", "gender": "F",
    "nationalId": "9999999999999"}})
s = c.post(B + "/idrepository/v1/identity/search",
           json={"fullName": "NDIAYE Awa", "dateOfBirth": "1984-03-12"}).json()
check("verdict AMBIGUOUS", s["verdict"] == "AMBIGUOUS", "%s (%d candidats)" % (s["verdict"], s["count"]))

print("\n7. Deces : desactivation et revocation en chaine")
r = c.patch(B + "/idrepository/v1/identity/uin/%s" % UIN_A,
            json={"status": "DEACTIVATED", "reason": "deces DKR-2026-D000004"})
check("patch 200", r.status_code == 200, r.status_code)
check("statut DEACTIVATED", r.json()["status"] == "DEACTIVATED")
check("motif conserve", r.json()["deactivationReason"] == "deces DKR-2026-D000004")
check("VID revoque en chaine", c.get(B + "/v1/vidgenerator/vid/%s" % VID_A).status_code == 410)
check("aucun VID neuf pour un defunt",
      c.post(B + "/v1/vidgenerator/vid", json={"uin": UIN_A}).status_code == 409)
h = c.get(B + "/actuator/health").json()
check("compteurs coherents", h["identity_count"] == 2 and h["stale_reservations"] == 0, json.dumps(h))

print("\n8. Mise a jour d'acte")
r = c.patch(B + "/idrepository/v1/identity/uin/%s" % u2,
            json={"identity": {"dateOfBirth": "1985-03-12"}}).json()
check("date corrigee", r["identity"]["dateOfBirth"] == "1985-03-12")
check("le reste conserve", r["identity"]["fullName"] == "NDIAYE Awa")

print("\n=== %d OK / %d ECHEC ===" % (ok, fails))
raise SystemExit(1 if fails else 0)
