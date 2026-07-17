# -*- coding: utf-8 -*-
"""Refonte nomenclature senegalaise dans Hearth (execute dans le pod bridge).
- 14 regions : jurisdictionType DISTRICT -> STATE, partOf -> Location/0, noms accentues
- 45 departements : LOCATION_LEVEL_3 -> DISTRICT (partOf region), noms accentues
- + departement Keur Massar (region Dakar, cree 2021) + son bureau d'etat civil
- ancien STATE "Senegal" supprime
- facilities existantes repointees vers leur departement
- carte sanitaire : ~55 structures publiques (hopitaux EPS + centres de sante departementaux)
- export du locations.json complet (ASCII) vers /tmp/locations-sn.json
IDEMPOTENT : rejouable sans effet de bord.
"""
import json
import os
import unicodedata
import uuid
from datetime import datetime

from pymongo import MongoClient
from bson import ObjectId

db = MongoClient(os.environ["MONGO_URL"]).get_default_database()
LOC = db["Location"]

def noacc(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

# region -> (code, nom accentue, {departement ascii -> nom accentue})
SN = {
    "Dakar": ("DKR", "Dakar", {"Dakar": "Dakar", "Pikine": "Pikine", "Guediawaye": "Guédiawaye",
                                "Rufisque": "Rufisque", "Keur Massar": "Keur Massar"}),
    "Thies": ("THS", "Thiès", {"Thies": "Thiès", "Mbour": "Mbour", "Tivaouane": "Tivaouane"}),
    "Diourbel": ("DBL", "Diourbel", {"Diourbel": "Diourbel", "Bambey": "Bambey", "Mbacke": "Mbacké"}),
    "Fatick": ("FCK", "Fatick", {"Fatick": "Fatick", "Foundiougne": "Foundiougne", "Gossas": "Gossas"}),
    "Kaolack": ("KLK", "Kaolack", {"Kaolack": "Kaolack", "Guinguineo": "Guinguinéo", "Nioro du Rip": "Nioro du Rip"}),
    "Kaffrine": ("KFF", "Kaffrine", {"Kaffrine": "Kaffrine", "Birkilane": "Birkelane", "Koungheul": "Koungheul",
                                      "Malem-Hodar": "Malem-Hodar"}),
    "Louga": ("LGA", "Louga", {"Louga": "Louga", "Kebemer": "Kébémer", "Linguere": "Linguère"}),
    "Saint-Louis": ("STL", "Saint-Louis", {"Saint-Louis": "Saint-Louis", "Dagana": "Dagana", "Podor": "Podor"}),
    "Matam": ("MTM", "Matam", {"Matam": "Matam", "Kanel": "Kanel", "Ranerou": "Ranérou"}),
    "Tambacounda": ("TMB", "Tambacounda", {"Tambacounda": "Tambacounda", "Bakel": "Bakel",
                                            "Goudiry": "Goudiry", "Koumpentoum": "Koumpentoum"}),
    "Kedougou": ("KDG", "Kédougou", {"Kedougou": "Kédougou", "Salemata": "Salémata", "Saraya": "Saraya"}),
    "Kolda": ("KLD", "Kolda", {"Kolda": "Kolda", "Medina Yoro Foulah": "Médina Yoro Foulah",
                                "Velingara": "Vélingara"}),
    "Sedhiou": ("SDH", "Sédhiou", {"Sedhiou": "Sédhiou", "Bounkiling": "Bounkiling", "Goudomp": "Goudomp"}),
    "Ziguinchor": ("ZGR", "Ziguinchor", {"Ziguinchor": "Ziguinchor", "Bignona": "Bignona", "Oussouye": "Oussouye"}),
}

# carte sanitaire publique (source : sante.gouv.sn / esante.sn / sensante.net) : nom -> departement ascii
FACILITIES = {
    "Hopital Principal de Dakar": "Dakar", "Hopital Aristide Le Dantec": "Dakar",
    "CHNU de Fann": "Dakar", "Hopital Abass Ndao": "Dakar",
    "CHN d'Enfants Albert Royer": "Dakar", "Hopital Militaire de Ouakam": "Dakar",
    "Hopital Philippe Maguilen Senghor": "Dakar",
    "Hopital National de Pikine": "Pikine", "CHN Psychiatrique de Thiaroye": "Pikine",
    "Hopital Dalal Jamm": "Guediawaye", "Maternite Roi Baudouin Guediawaye": "Guediawaye",
    "Hopital de Keur Massar": "Keur Massar",
    "Hopital Youssou Mbargane Diop de Rufisque": "Rufisque",
    "Hopital des Enfants de Diamniadio": "Rufisque",
    "Hopital Regional Amadou Sakhir Ndieguene de Thies": "Thies",
    "Hopital Saint Jean de Dieu de Thies": "Thies", "Centre de Sante de Pout": "Thies",
    "Hopital Departemental de Mbour": "Mbour",
    "Hopital Abdou Aziz Sy Dabakh de Tivaouane": "Tivaouane", "Centre de Sante de Mekhe": "Tivaouane",
    "Hopital Regional Heinrich Lubke de Diourbel": "Diourbel",
    "Centre de Sante de Bambey": "Bambey",
    "Hopital Matlaboul Fawzaini de Touba": "Mbacke", "Hopital de Ndamatou Touba": "Mbacke",
    "Hopital Regional de Fatick": "Fatick", "Centre de Sante de Foundiougne": "Foundiougne",
    "Centre de Sante de Gossas": "Gossas",
    "Hopital Regional El Hadji Ibrahima Niass de Kaolack": "Kaolack",
    "Centre de Sante de Guinguineo": "Guinguineo", "Centre de Sante de Nioro du Rip": "Nioro du Rip",
    "Hopital Thierno Birahim Ndao de Kaffrine": "Kaffrine",
    "Centre de Sante de Birkelane": "Birkilane", "Centre de Sante de Koungheul": "Koungheul",
    "Centre de Sante de Malem-Hodar": "Malem-Hodar",
    "Hopital Regional Amadou Sakhir Mbaye de Louga": "Louga",
    "Centre de Sante de Kebemer": "Kebemer", "Hopital Magatte Lo de Linguere": "Linguere",
    "Hopital Regional de Saint-Louis": "Saint-Louis",
    "Hopital de Richard-Toll": "Dagana", "Hopital de Ndioum": "Podor",
    "Hopital Regional de Ourossogui": "Matam", "Centre de Sante de Kanel": "Kanel",
    "Centre de Sante de Ranerou": "Ranerou",
    "Hopital Regional de Tambacounda": "Tambacounda", "Centre de Sante de Bakel": "Bakel",
    "Centre de Sante de Goudiry": "Goudiry", "Centre de Sante de Koumpentoum": "Koumpentoum",
    "Hopital Amath Dansokho de Kedougou": "Kedougou",
    "Centre de Sante de Salemata": "Salemata", "Centre de Sante de Saraya": "Saraya",
    "Hopital Regional de Kolda": "Kolda", "Centre de Sante de Velingara": "Velingara",
    "Centre de Sante de Medina Yoro Foulah": "Medina Yoro Foulah",
    "Hopital Amadou Tidiane Ba de Sedhiou": "Sedhiou",
    "Centre de Sante de Bounkiling": "Bounkiling", "Centre de Sante de Goudomp": "Goudomp",
    "Hopital Regional de Ziguinchor": "Ziguinchor", "Hopital de la Paix de Ziguinchor": "Ziguinchor",
    "Centre de Sante de Bignona": "Bignona", "Centre de Sante de Oussouye": "Oussouye",
}

stats = {"regions_maj": 0, "depts_maj": 0, "crees": 0, "facilities_creees": 0,
         "facilities_repointees": 0, "offices_repointes": 0, "supprimes": 0}

def set_jur(doc, jur):
    idents = [i for i in doc.get("identifier", []) if i.get("system") != "http://opencrvs.org/specs/id/jurisdiction-type"]
    idents.append({"system": "http://opencrvs.org/specs/id/jurisdiction-type", "value": jur})
    return idents

def internal_id_of(doc):
    for i in doc.get("identifier", []):
        if i.get("system") == "http://opencrvs.org/specs/id/internal-id":
            return i.get("value")
    return None

def find_internal(iid):
    return LOC.find_one({"identifier.system": "http://opencrvs.org/specs/id/internal-id",
                         "identifier.value": iid})

def new_location(name, part_ref, loc_type, jur, iid, physical="jdn"):
    rid = str(uuid.uuid4())
    doc = {
        "resourceType": "Location", "id": rid, "status": "active",
        "name": name, "alias": [noacc(name)],
        "identifier": [
            {"system": "http://opencrvs.org/specs/id/internal-id", "value": iid},
            {"system": "http://opencrvs.org/specs/id/statistical-code", "value": iid},
        ],
        "physicalType": {"coding": [{"system": "http://hl7.org/fhir/location-physical-type",
                                      "code": physical, "display": "Jurisdiction" if physical == "jdn" else "Building"}]},
        "type": {"coding": [{"system": "http://opencrvs.org/specs/location-type", "code": loc_type}]},
        "extension": [
            {"url": "http://opencrvs.org/specs/id/statistics-male-populations", "valueString": "[]"},
            {"url": "http://opencrvs.org/specs/id/statistics-female-populations", "valueString": "[]"},
            {"url": "http://opencrvs.org/specs/id/statistics-total-populations", "valueString": "[]"},
            {"url": "http://opencrvs.org/specs/id/statistics-crude-birth-rates", "valueString": "[]"},
        ],
        "partOf": {"reference": part_ref},
        "meta": {"lastUpdated": datetime.utcnow().isoformat() + "+00:00", "versionId": str(uuid.uuid4())},
        "_id": ObjectId(), "_request": {"method": "POST"},
    }
    if jur:
        doc["identifier"].append({"system": "http://opencrvs.org/specs/id/jurisdiction-type", "value": jur})
    LOC.insert_one(doc)
    stats["crees"] += 1
    return rid

def slug(s):
    return noacc(s).upper().replace(" ", "_").replace("-", "_")[:8]

# --- 1) regions : DISTRICT -> STATE + accents + partOf Location/0 ---
region_ids = {}
for reg_ascii, (code, reg_acc, depts) in SN.items():
    doc = find_internal("ADMIN_STRUCTURE_%s" % code)
    if not doc:
        rid = new_location(reg_acc, "Location/0", "ADMIN_STRUCTURE", "STATE", "ADMIN_STRUCTURE_%s" % code)
        region_ids[reg_ascii] = rid
        continue
    region_ids[reg_ascii] = doc["id"]
    LOC.update_one({"_id": doc["_id"]}, {"$set": {
        "name": reg_acc, "alias": [reg_ascii],
        "partOf.reference": "Location/0",
        "identifier": set_jur(doc, "STATE"),
    }})
    stats["regions_maj"] += 1

# --- 2) departements : LOCATION_LEVEL_3 -> DISTRICT + accents (+ creation Keur Massar) ---
dept_ids = {}
for reg_ascii, (code, reg_acc, depts) in SN.items():
    rid = region_ids[reg_ascii]
    for dep_ascii, dep_acc in depts.items():
        iid = "ADMIN_STRUCTURE_%s_%s" % (code, slug(dep_ascii))
        doc = find_internal(iid)
        if doc:
            dept_ids[dep_ascii] = doc["id"]
            LOC.update_one({"_id": doc["_id"]}, {"$set": {
                "name": dep_acc, "alias": [dep_ascii],
                "partOf.reference": "Location/%s" % rid,
                "identifier": set_jur(doc, "DISTRICT"),
            }})
            stats["depts_maj"] += 1
        else:
            did = new_location(dep_acc, "Location/%s" % rid, "ADMIN_STRUCTURE", "DISTRICT", iid)
            dept_ids[dep_ascii] = did
        # bureau d'etat civil du departement
        off_iid = "CRVS_OFFICE_%s_%s" % (code, slug(dep_ascii))
        if not find_internal(off_iid):
            new_location("Bureau Etat Civil %s" % dep_acc, "Location/%s" % dept_ids[dep_ascii],
                         "CRVS_OFFICE", "", off_iid, physical="bu")

# --- 3) ancien STATE Senegal : rattacher ses enfants restants puis supprimer ---
old_sn = LOC.find_one({"name": {"$in": ["Senegal", "Sénégal"]},
                        "identifier.value": "STATE"})
if old_sn:
    for child in LOC.find({"partOf.reference": "Location/%s" % old_sn["id"]}):
        # tout enfant restant (hors regions deja repointees) -> departement Dakar
        LOC.update_one({"_id": child["_id"]},
                       {"$set": {"partOf.reference": "Location/%s" % dept_ids["Dakar"]}})
    LOC.delete_one({"_id": old_sn["_id"]})
    stats["supprimes"] += 1

# --- 4) facilities + offices existants pointant vers une REGION -> repointer vers un departement ---
region_id_set = set(region_ids.values())
dept_by_region_chef = {SN[r][1]: dept_ids.get(next(iter(SN[r][2]))) for r in SN}
for doc in LOC.find({"type.coding.code": {"$in": ["HEALTH_FACILITY", "CRVS_OFFICE"]}}):
    part = (doc.get("partOf") or {}).get("reference", "")
    pid = part.split("/", 1)[1] if "/" in part else ""
    if pid in region_id_set:
        parent_reg = LOC.find_one({"id": pid})
        # Maternite Roi Baudouin -> Guediawaye, sinon chef-lieu de la region parente
        if "Roi Baudouin" in doc.get("name", ""):
            target = dept_ids["Guediawaye"]
        else:
            target = dept_by_region_chef.get(parent_reg.get("name"), dept_ids["Dakar"])
        LOC.update_one({"_id": doc["_id"]}, {"$set": {"partOf.reference": "Location/%s" % target}})
        key = "facilities_repointees" if doc["type"]["coding"][0]["code"] == "HEALTH_FACILITY" else "offices_repointes"
        stats[key] += 1

# --- 5) carte sanitaire ---
for fac_name, dep_ascii in FACILITIES.items():
    iid = "HEALTH_FACILITY_%s" % slug(fac_name.replace("Centre de Sante de ", "CS ").replace("Hopital ", "H "))
    # cle plus discriminante : slug complet du nom
    iid = "HEALTH_FACILITY_" + noacc(fac_name).upper().replace(" ", "_").replace("-", "_")[:48]
    if find_internal(iid):
        continue
    # ne pas dupliquer les 3 facilities historiques (matching par nom approx)
    if LOC.find_one({"type.coding.code": "HEALTH_FACILITY",
                     "name": {"$regex": "^%s$" % fac_name.replace("(", "\\(").replace(")", "\\)"),
                              "$options": "i"}}):
        continue
    new_location(fac_name, "Location/%s" % dept_ids[dep_ascii], "HEALTH_FACILITY", "", iid, physical="bu")
    stats["facilities_creees"] += 1

# --- 6) export locations.json (liste de resources, ASCII) ---
out = []
for doc in LOC.find({"type.coding.code": {"$in": ["ADMIN_STRUCTURE", "CRVS_OFFICE", "HEALTH_FACILITY"]}}):
    doc.pop("_id", None)
    doc.pop("_request", None)
    out.append(doc)
def _jdef(o):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)

with open("/tmp/locations-sn.json", "w") as f:
    json.dump(out, f, ensure_ascii=True, separators=(",", ":"), default=_jdef)

print("STATS:", json.dumps(stats))
print("TOTAUX:", json.dumps({
    "ADMIN_STRUCTURE": LOC.count_documents({"type.coding.code": "ADMIN_STRUCTURE"}),
    "CRVS_OFFICE": LOC.count_documents({"type.coding.code": "CRVS_OFFICE"}),
    "HEALTH_FACILITY": LOC.count_documents({"type.coding.code": "HEALTH_FACILITY"}),
    "locations_json_octets": os.path.getsize("/tmp/locations-sn.json"),
}))
print("EXPORT: /tmp/locations-sn.json")
