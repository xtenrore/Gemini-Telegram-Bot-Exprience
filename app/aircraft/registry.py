"""Local aircraft type registry used by Telegram selection and alert filtering.

The registry is intentionally independent from Telegram handlers.  Runtime
classification is local and deterministic; no network or AI call is required.
The seed is organised around canonical ICAO type designators and can be updated
without changing menu or filtering code.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

CATEGORY_LABELS: dict[str, str] = {
    "widebody": "Widebodies",
    "narrowbody": "Narrowbodies",
    "regional_jet": "Regional Jets",
    "turboprop": "Turboprops",
    "cargo": "Cargo",
    "business_jet": "Business Jets",
    "military": "Military",
    "general_aviation": "General Aviation",
    "helicopter": "Helicopters",
    "classic_rare": "Classic / Rare",
    "other": "Other",
}
CATEGORY_ORDER = tuple(CATEGORY_LABELS)

# ICAO | manufacturer | model | family | categories | aliases
# Keep this as data, not menu logic.  New rows are picked up automatically by
# search, pagination, category browsing and filtering.
_DATA = r"""
A124|Antonov|An-124 Ruslan|An-124|cargo,military|AN124,Antonov 124
A140|Antonov|An-140|An-140|turboprop|Antonov 140
A148|Antonov|An-148|An-148|regional_jet|Antonov 148
A158|Antonov|An-158|An-148|regional_jet|Antonov 158
A225|Antonov|An-225 Mriya|An-225|cargo,classic_rare|AN225,Mriya
AN12|Antonov|An-12|An-12|cargo,military,classic_rare|Antonov 12
AN24|Antonov|An-24|An-24|turboprop,classic_rare|Antonov 24
AN26|Antonov|An-26|An-26|cargo,military,turboprop|Antonov 26
AN28|Antonov|An-28|An-28|turboprop|Antonov 28
AN30|Antonov|An-30|An-30|military,turboprop|Antonov 30
AN32|Antonov|An-32|An-32|cargo,military,turboprop|Antonov 32
AN72|Antonov|An-72 / An-74|An-72|cargo,military|Antonov 72,Antonov 74
A19N|Airbus|A319neo|A320neo|narrowbody|A319 neo,A319neo
A20N|Airbus|A320neo|A320neo|narrowbody|A320 neo,A320neo
A21N|Airbus|A321neo|A320neo|narrowbody|A321 neo,A321neo
A306|Airbus|A300-600|A300|widebody,classic_rare|A300-600,A300
A30B|Airbus|A300B2/B4|A300|widebody,classic_rare|A300B,A300
A310|Airbus|A310|A310|widebody,classic_rare|A310-200,A310-300
A318|Airbus|A318|A320|narrowbody|A318
A319|Airbus|A319|A320|narrowbody|A319
A320|Airbus|A320|A320|narrowbody|A320
A321|Airbus|A321|A320|narrowbody|A321
A332|Airbus|A330-200|A330|widebody|A330-200,A330
A333|Airbus|A330-300|A330|widebody|A330-300,A330
A337|Airbus|A330-700 BelugaXL|BelugaXL|cargo|Beluga XL,BelugaXL
A338|Airbus|A330-800|A330neo|widebody|A330-800,A330neo
A339|Airbus|A330-900|A330neo|widebody|A330-900,A330neo
A342|Airbus|A340-200|A340|widebody,classic_rare|A340-200,A340
A343|Airbus|A340-300|A340|widebody,classic_rare|A340-300,A340
A345|Airbus|A340-500|A340|widebody,classic_rare|A340-500,A340
A346|Airbus|A340-600|A340|widebody,classic_rare|A340-600,A340
A359|Airbus|A350-900|A350|widebody|A350-900,A350
A35K|Airbus|A350-1000|A350|widebody|A350-1000,A350
A388|Airbus|A380-800|A380|widebody|A380-800,A380
A3ST|Airbus|A300-600ST Beluga|Beluga|cargo,classic_rare|Beluga,Super Transporter
A400|Airbus|A400M Atlas|A400M|military,cargo|A400M,Atlas
BCS1|Airbus|A220-100|A220|narrowbody,regional_jet|A220-100,A220
BCS3|Airbus|A220-300|A220|narrowbody,regional_jet|A220-300,A220
B712|Boeing|717-200|717|narrowbody|B717,717
B721|Boeing|727-100|727|narrowbody,classic_rare|727-100,B727
B722|Boeing|727-200|727|narrowbody,classic_rare|727-200,B727
B732|Boeing|737-200|737|narrowbody,classic_rare|737-200,B737
B733|Boeing|737-300|737 Classic|narrowbody,classic_rare|737-300,B737
B734|Boeing|737-400|737 Classic|narrowbody,classic_rare|737-400,B737
B735|Boeing|737-500|737 Classic|narrowbody,classic_rare|737-500,B737
B736|Boeing|737-600|737 NG|narrowbody|737-600,B737
B737|Boeing|737-700|737 NG|narrowbody|737-700,B737
B738|Boeing|737-800|737 NG|narrowbody|737-800,B737
B739|Boeing|737-900 / 900ER|737 NG|narrowbody|737-900,B737
B37M|Boeing|737 MAX 7|737 MAX|narrowbody|MAX 7,737 MAX 7
B38M|Boeing|737 MAX 8|737 MAX|narrowbody|MAX 8,737 MAX 8
B39M|Boeing|737 MAX 9|737 MAX|narrowbody|MAX 9,737 MAX 9
B3XM|Boeing|737 MAX 10|737 MAX|narrowbody|MAX 10,737 MAX 10
B741|Boeing|747-100|747|widebody,classic_rare|747-100,B747
B742|Boeing|747-200|747|widebody,classic_rare|747-200,B747
B743|Boeing|747-300|747|widebody,classic_rare|747-300,B747
B744|Boeing|747-400|747|widebody,classic_rare|747-400,B747
B748|Boeing|747-8|747|widebody|747-8,B747
B74S|Boeing|747SP|747|widebody,classic_rare|747 SP,747SP
B752|Boeing|757-200|757|narrowbody|757-200,B757
B753|Boeing|757-300|757|narrowbody|757-300,B757
B762|Boeing|767-200|767|widebody|767-200,B767
B763|Boeing|767-300 / 300ER|767|widebody|767-300,B767
B764|Boeing|767-400ER|767|widebody|767-400,B767
B772|Boeing|777-200 / 200ER|777|widebody|777-200,B777
B773|Boeing|777-300|777|widebody|777-300,B777
B77L|Boeing|777-200LR / 777F|777|widebody|777F,777-200LR,B777
B77W|Boeing|777-300ER|777|widebody|777-300ER,B777
B778|Boeing|777-8|777X|widebody|777-8,777X
B779|Boeing|777-9|777X|widebody|777-9,777X
B788|Boeing|787-8 Dreamliner|787|widebody|787-8,B787,Dreamliner
B789|Boeing|787-9 Dreamliner|787|widebody|787-9,B787,Dreamliner
B78X|Boeing|787-10 Dreamliner|787|widebody|787-10,B787,Dreamliner
B461|BAE|BAe 146-100|BAe 146|regional_jet,classic_rare|BAE146,146-100
B462|BAE|BAe 146-200|BAe 146|regional_jet,classic_rare|BAE146,146-200
B463|BAE|BAe 146-300|BAe 146|regional_jet,classic_rare|BAE146,146-300
RJ70|BAE|Avro RJ70|Avro RJ|regional_jet,classic_rare|Avro 70
RJ85|BAE|Avro RJ85|Avro RJ|regional_jet,classic_rare|Avro 85
RJ1H|BAE|Avro RJ100|Avro RJ|regional_jet,classic_rare|Avro 100
CRJ1|Bombardier|CRJ100|CRJ|regional_jet|CRJ100
CRJ2|Bombardier|CRJ200|CRJ|regional_jet|CRJ200
CRJ7|Bombardier|CRJ700|CRJ|regional_jet|CRJ700
CRJ9|Bombardier|CRJ900|CRJ|regional_jet|CRJ900
CRJX|Bombardier|CRJ1000|CRJ|regional_jet|CRJ1000
E170|Embraer|E170|E-Jet|regional_jet|Embraer 170,E-Jet
E75L|Embraer|E175|E-Jet|regional_jet|Embraer 175,E175
E190|Embraer|E190|E-Jet|regional_jet|Embraer 190,E-Jet
E195|Embraer|E195|E-Jet|regional_jet|Embraer 195,E-Jet
E290|Embraer|E190-E2|E-Jet E2|regional_jet|E190-E2,E2
E295|Embraer|E195-E2|E-Jet E2|regional_jet|E195-E2,E2
AT43|ATR|ATR 42-300/320|ATR 42|turboprop|ATR42,ATR 42
AT45|ATR|ATR 42-500|ATR 42|turboprop|ATR42-500,ATR 42
AT46|ATR|ATR 42-600|ATR 42|turboprop|ATR42-600,ATR 42
AT72|ATR|ATR 72-200|ATR 72|turboprop|ATR72,ATR 72
AT75|ATR|ATR 72-500|ATR 72|turboprop|ATR72-500,ATR 72
AT76|ATR|ATR 72-600|ATR 72|turboprop|ATR72-600,ATR 72
DH8A|De Havilland Canada|Dash 8-100|Dash 8|turboprop|DHC-8,Q100
DH8B|De Havilland Canada|Dash 8-200|Dash 8|turboprop|DHC-8,Q200
DH8C|De Havilland Canada|Dash 8-300|Dash 8|turboprop|DHC-8,Q300
DH8D|De Havilland Canada|Dash 8-400|Dash 8|turboprop|DHC-8,Q400
SF34|Saab|Saab 340|Saab 340|turboprop|Saab 340
SB20|Saab|Saab 2000|Saab 2000|turboprop|Saab 2000
F50|Fokker|Fokker 50|Fokker 50|turboprop,classic_rare|Fokker 50
F70|Fokker|Fokker 70|Fokker 70|regional_jet,classic_rare|Fokker 70
F100|Fokker|Fokker 100|Fokker 100|regional_jet,classic_rare|Fokker 100
MD11|McDonnell Douglas|MD-11|MD-11|widebody,classic_rare|MD11
DC10|McDonnell Douglas|DC-10|DC-10|widebody,classic_rare|DC10
DC9|McDonnell Douglas|DC-9|DC-9|narrowbody,classic_rare|DC9
MD80|McDonnell Douglas|MD-80|MD-80|narrowbody,classic_rare|MD80
MD81|McDonnell Douglas|MD-81|MD-80|narrowbody,classic_rare|MD81
MD82|McDonnell Douglas|MD-82|MD-80|narrowbody,classic_rare|MD82
MD83|McDonnell Douglas|MD-83|MD-80|narrowbody,classic_rare|MD83
MD87|McDonnell Douglas|MD-87|MD-80|narrowbody,classic_rare|MD87
MD88|McDonnell Douglas|MD-88|MD-80|narrowbody,classic_rare|MD88
IL62|Ilyushin|Il-62|Il-62|widebody,classic_rare|Ilyushin 62
IL76|Ilyushin|Il-76|Il-76|cargo,military,classic_rare|Ilyushin 76
IL86|Ilyushin|Il-86|Il-86|widebody,classic_rare|Ilyushin 86
IL96|Ilyushin|Il-96|Il-96|widebody,classic_rare|Ilyushin 96
T134|Tupolev|Tu-134|Tu-134|narrowbody,classic_rare|TU134,Tupolev 134
T154|Tupolev|Tu-154|Tu-154|narrowbody,classic_rare|TU154,Tupolev 154
T204|Tupolev|Tu-204 / Tu-214|Tu-204|narrowbody,classic_rare|TU204,TU214
YK40|Yakovlev|Yak-40|Yak-40|regional_jet,classic_rare|YAK40
YK42|Yakovlev|Yak-42|Yak-42|regional_jet,classic_rare|YAK42
SU95|Sukhoi|Superjet 100|SSJ100|regional_jet|SSJ100,Sukhoi Superjet
C919|COMAC|C919|C919|narrowbody|COMAC C919
AJ27|COMAC|ARJ21 / C909|C909|regional_jet|ARJ21,C909
GLF4|Gulfstream|GIV / G450|Gulfstream IV|business_jet|G450,GIV,Gulfstream 4
GLF5|Gulfstream|GV / G500 / G550|Gulfstream V|business_jet|G500,G550,GV,Gulfstream 5
GLF6|Gulfstream|G650 / G650ER|G650|business_jet|G650,G650ER,Gulfstream 650
GA5C|Gulfstream|G500|G500|business_jet|Gulfstream G500
GA6C|Gulfstream|G600|G600|business_jet|Gulfstream G600
GA7C|Gulfstream|G700|G700|business_jet|Gulfstream G700
GLEX|Bombardier|Global Express / 6000|Global|business_jet|Global Express,Global 6000
GL5T|Bombardier|Global 5000 / 5500|Global|business_jet|Global 5000,Global 5500
GL7T|Bombardier|Global 7500|Global|business_jet|Global 7500
CL30|Bombardier|Challenger 300|Challenger|business_jet|Challenger 300
CL35|Bombardier|Challenger 350|Challenger|business_jet|Challenger 350
CL60|Bombardier|Challenger 600 series|Challenger|business_jet|Challenger 600
F2TH|Dassault|Falcon 2000|Falcon|business_jet|Falcon 2000
F900|Dassault|Falcon 900|Falcon|business_jet|Falcon 900
F7X|Dassault|Falcon 7X|Falcon|business_jet|Falcon 7X
F8X|Dassault|Falcon 8X|Falcon|business_jet|Falcon 8X
C25A|Cessna|Citation CJ2|Citation|business_jet|Citation CJ2
C25B|Cessna|Citation CJ3|Citation|business_jet|Citation CJ3
C25C|Cessna|Citation CJ4|Citation|business_jet|Citation CJ4
C56X|Cessna|Citation Excel / XLS|Citation|business_jet|Citation XLS,Excel
C68A|Cessna|Citation Latitude|Citation|business_jet|Citation Latitude
C700|Cessna|Citation Longitude|Citation|business_jet|Citation Longitude
E50P|Embraer|Phenom 100|Phenom|business_jet|Phenom 100
E55P|Embraer|Phenom 300 / Praetor|Phenom|business_jet|Phenom 300,Praetor
PC24|Pilatus|PC-24|PC-24|business_jet|Pilatus PC24
H25B|Hawker Beechcraft|Hawker 800 / 900|Hawker|business_jet|Hawker 800,Hawker 900
LJ35|Learjet|Learjet 35|Learjet|business_jet,classic_rare|Lear 35,Learjet 35
LJ45|Learjet|Learjet 45|Learjet|business_jet|Learjet 45
LJ60|Learjet|Learjet 60|Learjet|business_jet|Learjet 60
PC12|Pilatus|PC-12|PC-12|general_aviation,turboprop|Pilatus PC12
TBM7|Daher|TBM 700|TBM|general_aviation,turboprop|TBM700
TBM8|Daher|TBM 850|TBM|general_aviation,turboprop|TBM850
TBM9|Daher|TBM 900 series|TBM|general_aviation,turboprop|TBM900,TBM930,TBM960
BE20|Beechcraft|King Air 200|King Air|general_aviation,turboprop|King Air 200
BE30|Beechcraft|King Air 300|King Air|general_aviation,turboprop|King Air 300
B350|Beechcraft|King Air 350|King Air|general_aviation,turboprop|King Air 350
C172|Cessna|172 Skyhawk|Cessna 172|general_aviation|Skyhawk,Cessna 172
C182|Cessna|182 Skylane|Cessna 182|general_aviation|Skylane,Cessna 182
C206|Cessna|206 Stationair|Cessna 206|general_aviation|Stationair,Cessna 206
C208|Cessna|208 Caravan|Caravan|general_aviation,turboprop|Caravan,Cessna 208
PA28|Piper|PA-28 Cherokee|PA-28|general_aviation|Cherokee,Piper 28
PA32|Piper|PA-32 Cherokee Six|PA-32|general_aviation|Cherokee Six,Piper 32
PA46|Piper|PA-46 Malibu / Meridian|PA-46|general_aviation,turboprop|Malibu,Meridian
SR20|Cirrus|SR20|SR|general_aviation|Cirrus SR20
SR22|Cirrus|SR22|SR|general_aviation|Cirrus SR22
DA40|Diamond|DA40|DA40|general_aviation|Diamond DA40
DA42|Diamond|DA42 Twin Star|DA42|general_aviation|Diamond DA42,Twin Star
DA62|Diamond|DA62|DA62|general_aviation|Diamond DA62
P28A|Piper|PA-28 family|PA-28|general_aviation|PA28,Piper Warrior,Archer
R44|Robinson|R44|R44|helicopter|Robinson R44
R66|Robinson|R66|R66|helicopter|Robinson R66
B06|Bell|206 JetRanger|Bell 206|helicopter|Bell 206,JetRanger
B407|Bell|407|Bell 407|helicopter|Bell 407
B412|Bell|412|Bell 412|helicopter|Bell 412
B429|Bell|429|Bell 429|helicopter|Bell 429
AS50|Airbus Helicopters|H125 / AS350|AS350|helicopter|AS350,H125,Squirrel
EC35|Airbus Helicopters|H135 / EC135|H135|helicopter|EC135,H135
EC45|Airbus Helicopters|H145 / EC145|H145|helicopter|EC145,H145
EC55|Airbus Helicopters|H155 / EC155|H155|helicopter|EC155,H155
AW09|Leonardo|AW109|AW109|helicopter|Agusta 109,AW109
AW13|Leonardo|AW139|AW139|helicopter|Agusta 139,AW139
AW18|Leonardo|AW189|AW189|helicopter|Agusta 189,AW189
S76|Sikorsky|S-76|S-76|helicopter|Sikorsky 76
S92|Sikorsky|S-92|S-92|helicopter|Sikorsky 92
H60|Sikorsky|UH-60 Black Hawk|H-60|helicopter,military|Black Hawk,UH60
S70|Sikorsky|S-70|H-60|helicopter,military|S70,Black Hawk
H47|Boeing|CH-47 Chinook|Chinook|helicopter,military|CH47,Chinook
V22|Bell Boeing|V-22 Osprey|V-22|military|Osprey,MV22,CV22
C17|Boeing|C-17 Globemaster III|C-17|military,cargo|C17A,Globemaster
C5M|Lockheed Martin|C-5M Super Galaxy|C-5|military,cargo|C5,Super Galaxy
C130|Lockheed Martin|C-130 Hercules|C-130|military,cargo|Hercules,C130H
C30J|Lockheed Martin|C-130J Super Hercules|C-130|military,cargo|C130J,Super Hercules
K35R|Boeing|KC-135R Stratotanker|KC-135|military|KC135,Stratotanker
K46|Boeing|KC-46A Pegasus|KC-46|military|KC46,Pegasus
A10|Fairchild Republic|A-10 Thunderbolt II|A-10|military|Warthog,A10C
F16|General Dynamics|F-16 Fighting Falcon|F-16|military|F16C,F16D,Fighting Falcon
F18|McDonnell Douglas|F/A-18 Hornet|F/A-18|military|FA18,Hornet
F22|Lockheed Martin|F-22 Raptor|F-22|military|F22A,Raptor
F35|Lockheed Martin|F-35 Lightning II|F-35|military|F35A,F35B,F35C,Lightning II
B1|Rockwell|B-1B Lancer|B-1|military|B1B,Lancer
B2|Northrop Grumman|B-2 Spirit|B-2|military|B2A,Spirit
B52|Boeing|B-52 Stratofortress|B-52|military,classic_rare|B52H,Stratofortress
E3CF|Boeing|E-3 Sentry|E-3|military|AWACS,E3
P8|Boeing|P-8 Poseidon|P-8|military|P8A,Poseidon
RQ4|Northrop Grumman|RQ-4 Global Hawk|RQ-4|military|Global Hawk
MQ9|General Atomics|MQ-9 Reaper|MQ-9|military|Reaper
U2|Lockheed|U-2 Dragon Lady|U-2|military,classic_rare|Dragon Lady
CONC|Aerospatiale BAC|Concorde|Concorde|classic_rare|Concorde
L104|Lockheed|L-1049 Super Constellation|Constellation|classic_rare|Super Constellation,Connie
DC3|Douglas|DC-3 / C-47|DC-3|classic_rare|C47,Dakota,DC3
B703|Boeing|707|707|classic_rare|B707,Boeing 707
A748|Hawker Siddeley|HS 748|HS 748|classic_rare,turboprop|HS748
"""


@dataclass(frozen=True, slots=True)
class AircraftType:
    code: str
    manufacturer: str
    model: str
    family: str
    categories: tuple[str, ...]
    aliases: tuple[str, ...] = ()

    @property
    def search_text(self) -> str:
        return " ".join((self.code, self.manufacturer, self.model, self.family, *self.aliases)).casefold()


def _load() -> dict[str, AircraftType]:
    result: dict[str, AircraftType] = {}
    for raw in _DATA.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        code, manufacturer, model, family, categories, aliases = raw.split("|", 5)
        code = code.strip().upper()
        result[code] = AircraftType(
            code=code,
            manufacturer=manufacturer.strip(),
            model=model.strip(),
            family=family.strip(),
            categories=tuple(c.strip() for c in categories.split(",") if c.strip()),
            aliases=tuple(a.strip() for a in aliases.split(",") if a.strip()),
        )
    return result


AIRCRAFT_TYPES = _load()

# Operator roles are additive.  Shared passenger/cargo airframes such as B744,
# B748 and B77L are not blindly treated as cargo just because a freighter
# variant exists; a known cargo operator can add the cargo role at runtime.
CARGO_OPERATOR_ICAO = frozenset({
    "FDX", "UPS", "CLX", "CKS", "GTI", "ABW", "BOX", "NCR", "PAC", "SRR",
    "QY", "BCS", "TAY", "ASL", "KAL", "ETD", "THY",
})


def get_aircraft_type(code: str | None) -> AircraftType | None:
    return AIRCRAFT_TYPES.get(str(code or "").strip().upper())


def search_aircraft(query: str, *, limit: int = 50) -> list[AircraftType]:
    """Search canonical code, model, manufacturer, family and aliases."""
    tokens = [t for t in re.split(r"\s+", query.casefold().strip()) if t]
    if not tokens:
        return []
    ranked: list[tuple[int, AircraftType]] = []
    compact = "".join(tokens).upper()
    for item in AIRCRAFT_TYPES.values():
        haystack = item.search_text
        if not all(token in haystack for token in tokens):
            continue
        score = 0
        if item.code == compact:
            score -= 100
        elif item.code.startswith(compact):
            score -= 50
        if item.model.casefold().startswith(query.casefold().strip()):
            score -= 20
        ranked.append((score, item))
    ranked.sort(key=lambda pair: (pair[0], pair[1].manufacturer, pair[1].model, pair[1].code))
    return [item for _, item in ranked[: max(1, limit)]]


def category_members(category: str) -> list[AircraftType]:
    return sorted(
        (item for item in AIRCRAFT_TYPES.values() if category in item.categories),
        key=lambda item: (item.manufacturer, item.model, item.code),
    )


def categories_for(code: str | None, operator_icao: str | None = None) -> tuple[str, ...]:
    item = get_aircraft_type(code)
    categories = list(item.categories if item else ("other",))
    operator = str(operator_icao or "").strip().upper()
    if operator in CARGO_OPERATOR_ICAO and "cargo" not in categories:
        categories.insert(0, "cargo")
    return tuple(dict.fromkeys(categories))


def primary_category(code: str | None, operator_icao: str | None = None) -> str:
    cats = categories_for(code, operator_icao)
    # Role categories win over airframe size when known.
    for preferred in ("cargo", "military", "helicopter", "business_jet"):
        if preferred in cats:
            return preferred
    for category in CATEGORY_ORDER:
        if category in cats:
            return category
    return "other"
