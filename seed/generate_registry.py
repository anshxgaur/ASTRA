"""Generate the canonical institute registry (internal single source of truth).

This file is used ONLY by the seeding layer. None of the five mock sources
expose it — each database keeps its own (noisy) representation of the same
real-world institutes, which is exactly the fragmentation being demonstrated.

Deterministic: fixed seed -> identical registry every run, so re-seeding is
idempotent and the planted conflicts stay stable across runs.

Output: institute_registry.json (project root)
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "institute_registry.json"

SEED = 20260816
COUNT = 500

# ── states/UTs weighted by real institute density (population share) ──────
# state -> (weight, [districts])  — districts are the seed pool; city == district HQ
STATES_DISTRICTS: dict[str, tuple[float, list[str]]] = {
    "Maharashtra": (0.105, ["Nagpur", "Pune", "Nashik", "Aurangabad", "Kolhapur", "Solapur", "Amravati"]),
    "Tamil Nadu": (0.095, ["Chennai", "Coimbatore", "Salem", "Thanjavur", "Vellore", "Tirunelveli", "Madurai"]),
    "Uttar Pradesh": (0.095, ["Lucknow", "Kanpur", "Varanasi", "Meerut", "Agra", "Allahabad", "Ghaziabad"]),
    "Karnataka": (0.085, ["Bengaluru", "Belagavi", "Hubballi", "Mysuru", "Ballari", "Davanagere", "Mangaluru"]),
    "Andhra Pradesh": (0.070, ["Visakhapatnam", "Guntur", "Kurnool", "Kadapa", "Vizianagaram", "Anantapur", "Vijayawada"]),
    "Telangana": (0.065, ["Hyderabad", "Warangal", "Karimnagar", "Nizamabad", "Khammam", "Adilabad", "Siddipet"]),
    "Gujarat": (0.060, ["Ahmedabad", "Rajkot", "Surat", "Vadodara", "Bhavnagar", "Junagadh", "Gandhinagar"]),
    "Rajasthan": (0.055, ["Jaipur", "Jodhpur", "Kota", "Udaipur", "Bikaner", "Ajmer", "Alwar"]),
    "Madhya Pradesh": (0.050, ["Indore", "Bhopal", "Jabalpur", "Gwalior", "Ujjain", "Rewa", "Sagar"]),
    "West Bengal": (0.045, ["Kolkata", "Howrah", "Durgapur", "Kharagpur", "Siliguri", "Bardhaman", "Haldia"]),
    "Kerala": (0.035, ["Thiruvananthapuram", "Kochi", "Kozhikode", "Thrissur", "Kannur", "Kollam"]),
    "Punjab": (0.030, ["Ludhiana", "Amritsar", "Jalandhar", "Patiala", "Bathinda", "Mohali"]),
    "Haryana": (0.030, ["Gurugram", "Faridabad", "Panipat", "Kurukshetra", "Ambala", "Rohtak"]),
    "Bihar": (0.025, ["Patna", "Gaya", "Muzaffarpur", "Bhagalpur", "Darbhanga", "Purnia"]),
    "Odisha": (0.025, ["Bhubaneswar", "Cuttack", "Rourkela", "Berhampur", "Sambalpur", "Balasore"]),
    "Delhi": (0.020, ["New Delhi", "South Delhi", "West Delhi", "North Delhi"]),
    "Assam": (0.015, ["Guwahati", "Jorhat", "Silchar", "Dibrugarh", "Tezpur"]),
    "Jharkhand": (0.015, ["Ranchi", "Jamshedpur", "Dhanbad", "Bokaro"]),
    "Chhattisgarh": (0.015, ["Raipur", "Bhilai", "Bilaspur", "Korba"]),
    "Uttarakhand": (0.012, ["Dehradun", "Haridwar", "Nainital", "Roorkee"]),
    "Himachal Pradesh": (0.010, ["Shimla", "Hamirpur", "Mandi", "Solan"]),
    "Jammu and Kashmir": (0.010, ["Srinagar", "Jammu", "Anantnag"]),
    "Goa": (0.005, ["North Goa", "South Goa"]),
    "Chandigarh": (0.004, ["Chandigarh"]),
    "Puducherry": (0.003, ["Puducherry", "Karaikal"]),
    "Tripura": (0.003, ["Agartala", "Udaipur"]),
    "Manipur": (0.002, ["Imphal"]),
    "Meghalaya": (0.002, ["Shillong", "Tura"]),
    "Sikkim": (0.002, ["Gangtok"]),
    "Nagaland": (0.002, ["Dimapur", "Kohima"]),
    "Mizoram": (0.002, ["Aizawl"]),
    "Arunachal Pradesh": (0.002, ["Itanagar"]),
    "Andaman and Nicobar": (0.001, ["Port Blair"]),
    "Dadra and Nagar Haveli": (0.001, ["Silvassa"]),
    "Daman and Diu": (0.001, ["Daman"]),
    "Ladakh": (0.001, ["Leh", "Kargil"]),
}

# ── institution type mix (spec: Engineering 55 / Mgmt 15 / Pharmacy 10 /
#    Architecture 5 / Polytechnic+Applied Arts 10 / Autonomous 5) ──────────
TYPE_WEIGHTS = [
    ("Engineering", 0.55),
    ("Management", 0.15),
    ("Pharmacy", 0.10),
    ("Architecture", 0.05),
    ("Polytechnic", 0.07),
    ("Applied Arts", 0.03),
    ("Autonomous", 0.05),
]
OWNERSHIP_WEIGHTS = [("Private", 0.68), ("Govt", 0.32)]

PREFIXES = [
    "Shri", "Sri Venkateswara", "Guru Nanak", "Mahatma Gandhi", "Dr. B.R. Ambedkar",
    "Siddhartha", "Vasavi", "Vignan", "Malla Reddy", "Sreenidhi", "CVR", "Anurag",
    "Gokaraju", "K.S.R.", "Matrusri", "Sree Dattha", "Sri Indu", "Geethanjali",
    "Bapatla", "Lendi", "Sreyas", "Aditya", "Srinivasa", "Raghu", "Vaagdevi",
    "Kakatiya", "Andhra", "Sri Sathya Sai", "Chaitanya", "Sinhgad", "Vishwakarma",
    "Lokmanya", "Sardar", "BMS", "RV", "Nitte", "NMAM", "Manipal", "Karunya",
    "Amrita", "Thapar", "Galgotias", "SRM", "VIT", "PSG", "Kongu", "Kumaraguru",
    "Thiagarajar", "Kalasalingam", "Bharat", "Ideal", "D.Y. Patil", "Symbiosis",
    "Kalinga", "Lovely", "Sharda", "Amity", "G.L. Bajaj", "Rajalakshmi", "Velammal",
    "Sathyabama", "Hindustan", "Vels", "Saveetha", "Bharath", "Jain", "Christ",
    "KLE", "PES", "BMS", "Reva", "Dayananda Sagar", "Girijananda Chowdhury",
]

SUFFIX_BY_TYPE: dict[str, list[str]] = {
    "Engineering": [
        "College of Engineering", "Institute of Technology", "Engineering College",
        "Institute of Engineering and Technology", "College of Engineering and Technology",
        "Institute of Technology and Science", "Institute of Science and Technology",
        "Institute of Engineering and Management",
    ],
    "Management": [
        "Institute of Management Studies", "Business School", "Institute of Management and Technology",
        "College of Management Studies", "School of Business Administration",
        "Institute of Business Management", "School of Management",
    ],
    "Pharmacy": [
        "College of Pharmacy", "Institute of Pharmaceutical Sciences", "Pharmacy College",
        "College of Pharmaceutical Sciences", "Institute of Pharmacy",
        "College of Pharmacy and Research",
    ],
    "Architecture": [
        "School of Architecture", "College of Architecture", "Institute of Architecture and Planning",
        "School of Planning and Architecture", "College of Architecture and Planning",
    ],
    "Polytechnic": [
        "Polytechnic", "Institute of Polytechnic Studies", "Institute of Engineering and Polytechnic",
        "Polytechnic College", "Institute of Polytechnic",
    ],
    "Applied Arts": [
        "College of Applied Arts", "Institute of Applied Arts", "College of Fine and Applied Arts",
        "Institute of Applied Arts and Crafts", "School of Applied Arts",
    ],
    "Autonomous": [
        "Institute of Technology", "Institute of Engineering and Technology",
        "Institute of Technology and Science", "Institute of Engineering and Management",
    ],
}

GOVT_TEMPLATES: dict[str, list[str]] = {
    "Engineering": ["Government College of Engineering, {d}", "Government Engineering College, {d}"],
    "Management": ["Government Institute of Management, {d}", "Government College of Management Studies, {d}"],
    "Pharmacy": ["Government College of Pharmacy, {d}", "Government Institute of Pharmaceutical Sciences, {d}"],
    "Architecture": ["Government School of Architecture, {d}", "Government College of Architecture, {d}"],
    "Polytechnic": ["Government Polytechnic, {d}", "Government Institute of Polytechnic Studies, {d}"],
    "Applied Arts": ["Government College of Applied Arts, {d}", "Government Institute of Applied Arts, {d}"],
    "Autonomous": ["Government Institute of Technology, {d}", "Government Institute of Engineering and Technology, {d}"],
}

AUTONOMOUS_TEMPLATES = [
    "National Institute of Technology, {d}",
    "Indian Institute of Information Technology, {d}",
    "Institute of Technology, {d}",
]


def _pick_state(rng: random.Random) -> str:
    states = list(STATES_DISTRICTS.keys())
    weights = [STATES_DISTRICTS[s][0] for s in states]
    return rng.choices(states, weights=weights, k=1)[0]


def _make_name(rng: random.Random, itype: str, ownership: str, district: str) -> str:
    if itype == "Autonomous":
        if rng.random() < 0.5:
            return rng.choice(AUTONOMOUS_TEMPLATES).format(d=district)
        return f"{rng.choice(PREFIXES)} {rng.choice(SUFFIX_BY_TYPE['Engineering'])}, {district}"
    if ownership == "Govt":
        return rng.choice(GOVT_TEMPLATES[itype]).format(d=district)
    return f"{rng.choice(PREFIXES)} {rng.choice(SUFFIX_BY_TYPE[itype])}, {district}"


def _valid_until(rng: random.Random) -> str:
    """NBA accreditation valid-until date in 2024–2031 (ISO, clean format)."""
    start = date(2024, 1, 1)
    return (start + timedelta(days=rng.randint(0, 365 * 7))).isoformat()


def _by_name(institutes: list[dict], name: str) -> dict:
    for inst in institutes:
        if inst["name"] == name:
            return inst
    raise KeyError(name)


def _conflict_plan(institutes: list[dict], rng: random.Random) -> dict:
    """Deterministic picks of institutes that will carry cross-source conflicts.

    Scaled to 500 institutes (target: 28 cross-source conflicts):
      10 approved-but-closed · 6 approved-but-unapproved-listed ·
      4 rejected-but-has-courses (each also gets a scholarship conflict) ·
      4 under-review-but-nba-accredited.
    Each source script reads this plan and plants its side of the contradiction.
    """
    def pick(*idx: int) -> list[str]:
        return [institutes[i]["name"] for i in idx]

    closed = pick(3, 41, 97, 158, 214, 276, 322, 389, 441, 487)
    unapproved = pick(9, 66, 133, 201, 268, 350)
    rejected = pick(13, 112, 233, 301)
    nba = pick(7, 88, 174, 259)

    for name in closed:
        _by_name(institutes, name)["approval_status"] = "Approved"
    for name in unapproved:
        _by_name(institutes, name)["approval_status"] = "Approved"
    for name in rejected:
        _by_name(institutes, name)["approval_status"] = "Rejected"
    for name in nba:
        _by_name(institutes, name)["approval_status"] = "Under Review"

    return {
        "approved_but_closed": closed,
        "approved_but_unapproved_listed": unapproved,
        "rejected_with_courses": rejected,
        "under_review_but_nba_accredited": nba,
    }


def build_registry(seed: int = SEED, count: int = COUNT) -> dict:
    rng = random.Random(seed)
    fake = Faker("en_IN")
    fake.seed_instance(seed)

    institutes: list[dict] = []
    used_names: set[str] = set()
    i = 0
    while len(institutes) < count:
        state = _pick_state(rng)
        district = rng.choice(STATES_DISTRICTS[state][1])
        itype = rng.choices([t for t, _ in TYPE_WEIGHTS],
                            weights=[w for _, w in TYPE_WEIGHTS], k=1)[0]
        ownership = rng.choices([o for o, _ in OWNERSHIP_WEIGHTS],
                                weights=[w for _, w in OWNERSHIP_WEIGHTS], k=1)[0]
        name = _make_name(rng, itype, ownership, district)
        if name in used_names:
            for d2 in rng.sample(STATES_DISTRICTS[state][1], k=len(STATES_DISTRICTS[state][1])):
                candidate = _make_name(rng, itype, ownership, d2)
                if candidate not in used_names:
                    name = candidate
                    break
        if name in used_names:
            name = f"{name} {len(institutes) + 1}"
        used_names.add(name)

        # current_status: ~85% active / 10% closed / 5% unapproved (AICTE ratios)
        current_status = rng.choices(
            ["active", "closed", "unapproved"], weights=[0.85, 0.10, 0.05])[0]
        approval_status = {
            "active": "Approved", "closed": "Rejected", "unapproved": "Under Review",
        }[current_status]

        nba_accredited = rng.random() < 0.35
        institutes.append({
            "id": f"INST_{len(institutes):03d}",
            "name": name,
            "state": state,
            "district": district,
            "city": district,
            "established_year": rng.randint(1960, 2023),
            "institution_type": itype,
            "ownership": ownership,
            "is_autonomous": rng.random() < 0.12,
            "nba_accredited": nba_accredited,
            "accreditation_valid_until": _valid_until(rng) if nba_accredited else "",
            "current_status": current_status,
            "approval_status": approval_status,
            "aicte_code": f"{rng.randint(1, 8)}-{rng.randint(10**9, 10**10 - 1)}",
        })
        i += 1

    plan = _conflict_plan(institutes, rng)
    return {
        "seed": seed,
        "count": len(institutes),
        "conflict_plan": plan,
        "institutes": institutes,
    }


def main() -> None:
    data = build_registry(count=COUNT)
    REGISTRY_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    plan = data["conflict_plan"]
    types: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for inst in data["institutes"]:
        types[inst["institution_type"]] = types.get(inst["institution_type"], 0) + 1
        statuses[inst["current_status"]] = statuses.get(inst["current_status"], 0) + 1
    print("=" * 60)
    print(f"[registry] wrote {len(data['institutes'])} canonical institutes -> {REGISTRY_PATH.name}")
    print(f"[registry] by type     : {dict(sorted(types.items()))}")
    print(f"[registry] by status   : {dict(sorted(statuses.items()))}")
    print(f"[registry] cross-source conflict carriers:")
    print(f"            approved-but-closed            : {len(plan['approved_but_closed'])}")
    print(f"            approved-but-unapproved-listed : {len(plan['approved_but_unapproved_listed'])}")
    print(f"            rejected-but-has-courses       : {len(plan['rejected_with_courses'])}")
    print(f"            under-review-but-nba-accredited: {len(plan['under_review_but_nba_accredited'])}")
    print(f"[registry] total conflict carriers: {sum(len(v) for v in plan.values())}")


if __name__ == "__main__":
    main()
