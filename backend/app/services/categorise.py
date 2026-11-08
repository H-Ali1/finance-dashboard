"""Turn a raw bank description into a merchant + category.

Order of precedence, highest first:
  1. a manual category set on the transaction in the UI  (never overwritten)
  2. a user rule from the category_rules table
  3. keyword match against the merchant/description
  4. the merchant category code (MCC) the card network supplied
  5. the provider's own category (Starling ships a decent one)
"""

from __future__ import annotations

import re

CATEGORIES = [
    "Income",
    "Groceries",
    "Eating Out",
    "Transport",
    "Fuel",
    "Shopping",
    "Bills & Utilities",
    "Rent & Mortgage",
    "Subscriptions",
    "Entertainment",
    "Health & Fitness",
    "Travel",
    "Holidays",
    "Cash",
    "Fees & Interest",
    "Insurance",
    "Education",
    "Gifts & Charity",
    "Savings & Investments",
    "Transfers",
    "Uncategorised",
]

# Noise that appears around the actual merchant name on UK statements.
_NOISE = re.compile(
    r"\b(card purchase|purchase|pos|contactless|chip and pin|visa|mastercard|debit|credit|"
    r"payment to|payment from|bill payment|faster payment|direct debit|standing order|"
    r"d/d|s/o|bgc|atm|ref(?:erence)?|gbp|on \d{2}[-/ ]\d{2}(?:[-/ ]\d{2,4})?)\b",
    re.IGNORECASE,
)
_TRAILING_JUNK = re.compile(r"[\d#*]{4,}|\b\d{2}[-/]\d{2}[-/]\d{2,4}\b")
_SUFFIXES = re.compile(r"\b(ltd|limited|plc|uk|gb|gbr|london|inc|llc|co)\b\.?", re.IGNORECASE)


# Payment processors that put the real merchant *after* the star. Anything else
# with a star ("AMAZON.CO.UK*MK4TR") has the merchant first and a reference after.
_PROCESSORS = {
    "paypal",
    "pp",
    "sq",
    "square",
    "sumup",
    "zettle",
    "izettle",
    "stripe",
    "wpy",
    "wp",
    "tsys",
    "worldpay",
    "shopify",
    "gsuite",
    "google",
    "fb",
    "meta",
}


def normalise_merchant(raw: str) -> str:
    """A stable key for grouping the same shop across statements."""
    text = (raw or "").strip()
    if not text:
        return ""

    # "PAYPAL *SPOTIFY" -> Spotify, but "AMAZON.CO.UK*MK4TR" stays Amazon.
    if "*" in text:
        head, tail = text.rsplit("*", 1)
        head_key = re.sub(r"[^a-z]", "", head.lower())
        tail_letters = re.sub(r"[^a-zA-Z]", "", tail)
        text = tail.strip() if head_key in _PROCESSORS and len(tail_letters) >= 3 else head.strip()

    text = _TRAILING_JUNK.sub(" ", text)
    text = _NOISE.sub(" ", text)
    text = _SUFFIXES.sub(" ", text)
    text = re.sub(r"[^a-zA-Z0-9&' ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    # Drop trailing store numbers: "tesco stores 3456" -> "tesco stores"
    text = re.sub(r"\s+\d+$", "", text)
    return text[:80]


def display_merchant(raw: str) -> str:
    key = normalise_merchant(raw)
    if not key:
        return (raw or "Unknown").strip()[:80] or "Unknown"
    return " ".join(word.capitalize() if len(word) > 2 else word.upper() for word in key.split())


_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "Groceries",
        (
            "tesco",
            "sainsbury",
            "asda",
            "morrison",
            "aldi",
            "lidl",
            "waitrose",
            "co-op",
            "coop",
            "iceland",
            "ocado",
            "marks spencer",
            "m s simply food",
            "spar",
            "budgens",
            "nisa",
            "farmfoods",
            "costcutter",
            "premier stores",
            "grocer",
            "supermarket",
            "food warehouse",
            "gettir",
            "getir",
            "zapp",
        ),
    ),
    (
        "Eating Out",
        (
            "mcdonald",
            "kfc",
            "burger king",
            "subway",
            "greggs",
            "nando",
            "pizza",
            "domino",
            "papa john",
            "wagamama",
            "pret",
            "costa",
            "starbucks",
            "caffe nero",
            "cafe",
            "coffee",
            "restaurant",
            "deliveroo",
            "uber eats",
            "ubereats",
            "just eat",
            "justeat",
            "takeaway",
            "kebab",
            "chicken",
            "sushi",
            "bakery",
            "five guys",
            "wingstop",
            "chipotle",
            "tortilla",
            "leon",
            "itsu",
            "gail",
            "dishoom",
            "bill s",
            "toby carvery",
            "harvester",
            "wetherspoon",
            "bar",
            "pub",
            "brewdog",
            "grill",
            "shawarma",
            "chai",
            "dessert",
        ),
    ),
    (
        "Transport",
        (
            "tfl",
            "transport for london",
            "trainline",
            "national rail",
            "lner",
            "avanti",
            "gwr",
            "northern rail",
            "thameslink",
            "southeastern",
            "uber",
            "bolt",
            "free now",
            "addison lee",
            "taxi",
            "cab",
            "bus",
            "stagecoach",
            "arriva",
            "megabus",
            "national express",
            "oyster",
            "parking",
            "ringgo",
            "justpark",
            "pay by phone",
            "paybyphone",
            "dart charge",
            "congestion",
            "ulez",
            "dvla",
            "mot",
            "halfords",
            "kwik fit",
            "national tyres",
            "euro car parts",
        ),
    ),
    (
        "Fuel",
        (
            "shell",
            "bp ",
            "esso",
            "texaco",
            "gulf",
            "jet ",
            "petrol",
            "fuel",
            "applegreen",
            "motor fuel",
            "ascona",
            "certas",
            "instavolt",
            "pod point",
            "podpoint",
            "gridserve",
            "osprey charging",
            "ionity",
            "bp pulse",
        ),
    ),
    (
        "Shopping",
        (
            "amazon",
            "amzn",
            "ebay",
            "argos",
            "currys",
            "john lewis",
            "next retail",
            "primark",
            "h m",
            "zara",
            "uniqlo",
            "asos",
            "boohoo",
            "shein",
            "temu",
            "sports direct",
            "jd sports",
            "nike",
            "adidas",
            "ikea",
            "b q",
            "b and q",
            "wickes",
            "screwfix",
            "toolstation",
            "homebase",
            "the range",
            "dunelm",
            "wilko",
            "poundland",
            "home bargains",
            "b m",
            "tk maxx",
            "matalan",
            "very co",
            "shop",
            "store",
            "vinted",
            "depop",
            "etsy",
            "aliexpress",
        ),
    ),
    (
        "Bills & Utilities",
        (
            "british gas",
            "octopus energy",
            "edf",
            "eon",
            "e on",
            "ovo energy",
            "scottish power",
            "shell energy",
            "utilita",
            "bulb",
            "thames water",
            "severn trent",
            "anglian water",
            "united utilities",
            "yorkshire water",
            "council tax",
            "tv licence",
            "tv licensing",
            "bt group",
            "bt broadband",
            "sky ",
            "virgin media",
            "talktalk",
            "plusnet",
            "hyperoptic",
            "community fibre",
            "vodafone",
            "ee ",
            "o2",
            "three",
            "giffgaff",
            "lebara",
            "lyca",
            "smarty",
            "id mobile",
            "voxi",
            "utility",
            "energy",
            "water",
        ),
    ),
    (
        "Rent & Mortgage",
        ("rent", "landlord", "letting", "mortgage", "nationwide bs", "property management", "housing"),
    ),
    (
        "Subscriptions",
        (
            "netflix",
            "spotify",
            "disney",
            "apple com bill",
            "apple services",
            "itunes",
            "google",
            "youtube",
            "amazon prime",
            "prime video",
            "now tv",
            "nowtv",
            "paramount",
            "hayu",
            "audible",
            "kindle unlimited",
            "dropbox",
            "icloud",
            "microsoft",
            "adobe",
            "openai",
            "anthropic",
            "claude ai",
            "chatgpt",
            "github",
            "notion",
            "canva",
            "figma",
            "linkedin",
            "duolingo",
            "strava",
            "patreon",
            "substack",
            "medium",
            "the times",
            "guardian",
            "telegraph",
            "financial times",
            "xbox",
            "playstation network",
            "nintendo",
        ),
    ),
    (
        "Entertainment",
        (
            "cinema",
            "odeon",
            "vue",
            "cineworld",
            "picturehouse",
            "everyman",
            "theatre",
            "ticketmaster",
            "see tickets",
            "eventbrite",
            "dice fm",
            "steam games",
            "playstation",
            "nintendo",
            "epic games",
            "bowling",
            "golf",
            "escape room",
            "museum",
            "zoo",
            "concert",
            "festival",
        ),
    ),
    (
        "Health & Fitness",
        (
            "pharmacy",
            "boots",
            "superdrug",
            "nhs",
            "dentist",
            "dental",
            "optician",
            "specsavers",
            "vision express",
            "doctor",
            "clinic",
            "hospital",
            "physio",
            "puregym",
            "pure gym",
            "the gym group",
            "david lloyd",
            "virgin active",
            "nuffield",
            "anytime fitness",
            "gymshark",
            "myprotein",
            "holland barrett",
            "bupa",
            "vitality",
        ),
    ),
    (
        "Travel",
        (
            "ryanair",
            "easyjet",
            "british airways",
            "wizz air",
            "jet2",
            "tui",
            "emirates",
            "qatar",
            "turkish airlines",
            "pegasus",
            "airbnb",
            "booking com",
            "hotels com",
            "expedia",
            "hostel",
            "hotel",
            "premier inn",
            "travelodge",
            "eurostar",
            "ferry",
            "p o ferries",
            "airport",
            "skyscanner",
            "lastminute",
            "on the beach",
        ),
    ),
    ("Cash", ("cash withdrawal", "atm", "link atm", "cardtronics", "notemachine", "cash machine")),
    (
        "Fees & Interest",
        (
            "interest",
            "overdraft",
            "late fee",
            "annual fee",
            "card fee",
            "service charge",
            "non sterling",
            "foreign transaction",
            "cash advance fee",
            "arrangement fee",
        ),
    ),
    (
        "Insurance",
        (
            "insurance",
            "insure",
            "admiral",
            "direct line",
            "aviva",
            "axa",
            "churchill",
            "hastings",
            "lv ",
            "esure",
            "quotemehappy",
            "comparethemarket",
            "confused com",
            "gocompare",
        ),
    ),
    (
        "Education",
        (
            "student loan",
            "slc ",
            "university",
            "college",
            "tuition",
            "udemy",
            "coursera",
            "pluralsight",
            "book depository",
            "waterstones",
            "blackwell",
        ),
    ),
    (
        "Gifts & Charity",
        (
            "charity",
            "justgiving",
            "gofundme",
            "oxfam",
            "cancer research",
            "british red cross",
            "unicef",
            "islamic relief",
            "gift",
            "donation",
            "zakat",
        ),
    ),
    (
        "Savings & Investments",
        (
            "trading 212",
            "trading212",
            "vanguard",
            "hargreaves",
            "aj bell",
            "freetrade",
            "invest",
            "coinbase",
            "kraken",
            "binance",
            "revolut savings",
            "moneybox",
            "plum",
            "chip ",
            "premium bonds",
            "ns i",
            "nsandi",
            "pension",
            "nutmeg",
            "wealthify",
            "isa ",
        ),
    ),
    (
        "Income",
        (
            "salary",
            "payroll",
            "wages",
            "hmrc",
            "dwp",
            "universal credit",
            "child benefit",
            "tax credit",
            "dividend",
            "refund",
            "reimbursement",
            "bonus",
            "stipend",
            "grant",
        ),
    ),
    (
        "Transfers",
        (
            "transfer",
            "trf",
            "own account",
            "savings goal",
            "space transfer",
            "monzo pot",
            "credit card payment",
            "cc payment",
            "payment received thank you",
            "payment thank you",
            "direct debit payment received",
        ),
    ),
]

# Card-network merchant category codes -> our categories (partial but useful).
_MCC = {
    **{c: "Groceries" for c in ("5411", "5422", "5451", "5462", "5499")},
    **{c: "Eating Out" for c in ("5811", "5812", "5813", "5814")},
    **{c: "Fuel" for c in ("5541", "5542", "5983")},
    **{c: "Transport" for c in ("4111", "4112", "4121", "4131", "4784", "7523", "7512")},
    **{c: "Travel" for c in ("3000", "3001", "4511", "4722", "7011", "3501")},
    **{c: "Health & Fitness" for c in ("5912", "8011", "8021", "8043", "8062", "7997", "7991")},
    **{c: "Entertainment" for c in ("7832", "7922", "7929", "7994", "7996")},
    **{c: "Bills & Utilities" for c in ("4814", "4816", "4899", "4900")},
    **{c: "Shopping" for c in ("5311", "5651", "5691", "5732", "5734", "5999", "5200", "5712")},
    **{c: "Cash" for c in ("6010", "6011")},
    **{c: "Fees & Interest" for c in ("6012", "6051")},
    "8398": "Gifts & Charity",
    "8220": "Education",
    "6300": "Insurance",
}

# Starling ships its own spendingCategory on every feed item.
_PROVIDER = {
    "GROCERIES": "Groceries",
    "EATING_OUT": "Eating Out",
    "TRANSPORT": "Transport",
    "SHOPPING": "Shopping",
    "BILLS_AND_SERVICES": "Bills & Utilities",
    "ENTERTAINMENT": "Entertainment",
    "GENERAL": "Uncategorised",
    "HOLIDAYS": "Holidays",
    "EXPENSES": "Shopping",
    "INCOME": "Income",
    "SAVING": "Savings & Investments",
    "FAMILY": "Shopping",
    "PERSONAL_CARE": "Health & Fitness",
    "LIFESTYLE": "Entertainment",
    "HOME": "Bills & Utilities",
    "GIFTS": "Gifts & Charity",
    "CHARITY": "Gifts & Charity",
    "PAYMENTS": "Transfers",
    "TRANSFERS": "Transfers",
}


def _keyword_category(haystack: str) -> str | None:
    for category, keywords in _KEYWORDS:
        for keyword in keywords:
            if keyword in haystack:
                return category
    return None


def categorise(
    *,
    merchant: str,
    description: str,
    amount_minor: int,
    mcc: str | None = None,
    provider_category: str | None = None,
    rules: list | None = None,
) -> tuple[str, str]:
    """Returns (category, source)."""
    merchant_key = normalise_merchant(merchant or description)
    description_key = (description or "").lower()

    for rule in rules or []:
        target = merchant_key if rule.field == "merchant" else description_key
        if _rule_matches(rule, target):
            return rule.category, "rule"

    haystack = f"{merchant_key} {description_key}"
    if match := _keyword_category(haystack):
        # An inbound payment matching a spend keyword is almost always a refund.
        if amount_minor > 0 and match not in {"Income", "Transfers", "Savings & Investments"}:
            return "Income", "auto"
        return match, "auto"

    if mcc and (mapped := _MCC.get(str(mcc).zfill(4))):
        return mapped, "auto"

    mapped = _PROVIDER.get(provider_category.upper()) if provider_category else None
    if mapped and mapped != "Uncategorised":
        return mapped, "auto"

    if amount_minor > 0:
        return "Income", "auto"
    return "Uncategorised", "auto"


def _rule_matches(rule, target: str) -> bool:
    pattern = (rule.pattern or "").lower()
    if not pattern or not target:
        return False
    if rule.match_type == "exact":
        return target.strip() == pattern.strip()
    if rule.match_type == "regex":
        try:
            return re.search(pattern, target, re.IGNORECASE) is not None
        except re.error:
            return False
    return pattern in target
