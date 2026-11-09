"""Turn a raw bank description into a merchant + category.

Order of precedence, highest first:
  1. a manual category set on the transaction in the UI  (never overwritten)
  2. a user rule from the category_rules table
  3. the kind of payment, where it settles the question (cash machine, bank
     fee, interest, a move between your own pots)
  4. keyword match against the merchant/description
  5. the merchant category code (MCC) the card network supplied
  6. the provider's own category (Starling's, which you can also set in the
     Starling app, and TrueLayer's classification for HSBC, Amex, ...)

Keywords match whole words, so "bar" finds "The Bar" but not "Barclays". The
keyword nearest the start of the description wins, since that's where the
brand is ("Tesco Heathrow Airport" is groceries), and of two starting at the
same word the longer one wins ("Amazon Prime" is a subscription).
"""

from __future__ import annotations

import re

CATEGORIES = [
    "Salary",
    "Tutoring",
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
    "Tax",
    "Education",
    "Gifts & Charity",
    "Savings & Investments",
    "Transfers",
    "Uncategorised",
]

# Money in that is income. "Income" is everything else that's earned or
# given: interest, benefits, dividends, a tax rebate.
INCOME_CATEGORIES = {"Salary", "Tutoring", "Income"}
# Money moving between your own pots: neither spending nor income.
NOT_SPENDING_OR_INCOME = {"Transfers", "Savings & Investments"}

# What kind of payment it was, in one vocabulary for every provider. Banks
# say it in their own words (Starling's `source`, TrueLayer's
# `transaction_category`, HSBC's "SO"/"DD"/"VIS" codes); providers translate.
PAYMENT_TYPES = (
    "card",  # a card purchase or a card refund
    "direct_debit",
    "standing_order",
    "transfer",  # a bank transfer to or from someone: Faster Payments, BACS, CHAPS
    "cash",  # cash machine
    "interest",
    "fee",
    "internal",  # between your own pots at the same bank (Starling spaces)
    "dividend",
)

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
    "iz",
    "stripe",
    "wpy",
    "wp",
    "tsys",
    "worldpay",
    "shopify",
    "sp",
    "crv",  # Curve
    "lsp",
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


# Keywords match as whole words (a trailing "s" or "es" is allowed, for
# "Sainsburys" and "Nando's"), against the merchant and description with
# punctuation turned into spaces: write "m s" for M&S, "co op" for Co-op.
# Avoid words that are also place or people's names ("Hastings", "Isa").
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "Groceries",
        (
            "tesco",
            "tesco express",
            "tesco extra",
            "tesco stores",
            "sainsbury",
            "asda",
            "morrison",
            "aldi",
            "lidl",
            "waitrose",
            "co op",
            "coop",
            "iceland",
            "ocado",
            "marks spencer",
            "marks and spencer",
            "m s simply food",
            "m s food",
            "spar",
            "budgens",
            "nisa",
            "farmfoods",
            "heron foods",
            "costcutter",
            "costco",
            "booker",
            "premier stores",
            "one stop",
            "londis",
            "whole foods",
            "planet organic",
            "amazon fresh",
            "grocer",
            "grocery",
            "groceries",
            "supermarket",
            "food warehouse",
            "gettir",
            "getir",
            "gopuff",
            "zapp",
        ),
    ),
    (
        "Eating Out",
        (
            "mcdonald",
            "kfc",
            "burger king",
            "burger",
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
            "joe the juice",
            "tim hortons",
            "krispy kreme",
            "restaurant",
            "deliveroo",
            "uber eats",
            "ubereats",
            "just eat",
            "justeat",
            "takeaway",
            "kebab",
            "chicken",
            "fish and chip",
            "chippy",
            "sushi",
            "bakery",
            "five guys",
            "wingstop",
            "popeyes",
            "taco bell",
            "chipotle",
            "tortilla",
            "leon",
            "itsu",
            "wasabi",
            "gail",
            "dishoom",
            "bill s",
            "zizzi",
            "prezzo",
            "franco manca",
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
            "too good to go",
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
            "southern railway",
            "c2c",
            "chiltern railways",
            "crosscountry",
            "scotrail",
            "transpennine",
            "heathrow express",
            "uber",
            "bolt",
            "free now",
            "addison lee",
            "taxi",
            "cab",
            "bus",
            "first bus",
            "stagecoach",
            "arriva",
            "megabus",
            "national express",
            "oyster",
            "santander cycles",
            "lime",
            "zipcar",
            "parking",
            "car park",
            "ncp",
            "q park",
            "apcoa",
            "ringgo",
            "justpark",
            "pay by phone",
            "paybyphone",
            "dart charge",
            "m6 toll",
            "congestion",
            "ulez",
            "dvla",
            "dvsa",
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
            "bp",
            "esso",
            "texaco",
            "gulf",
            "jet",
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
            "decathlon",
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
            "tkmaxx",
            "homesense",
            "matalan",
            "very co",
            "selfridges",
            "card factory",
            "smyths",
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
            "eon next",
            "ovo",
            "ovo energy",
            "so energy",
            "sse",
            "scottish power",
            "shell energy",
            "utilita",
            "bulb",
            "thames water",
            "severn trent",
            "anglian water",
            "united utilities",
            "yorkshire water",
            "southern water",
            "affinity water",
            "wessex water",
            "south east water",
            "dwr cymru",
            "council tax",
            "council",
            "tv licence",
            "tv licensing",
            "bt",
            "bt group",
            "bt broadband",
            "sky",
            "sky digital",
            "virgin media",
            "talktalk",
            "plusnet",
            "hyperoptic",
            "community fibre",
            "vodafone",
            "ee",
            "o2",
            "three",
            "giffgaff",
            "lebara",
            "lyca",
            "lycamobile",
            "smarty",
            "id mobile",
            "tesco mobile",
            "sky mobile",
            "virgin mobile",
            "voxi",
            "utility",
            "utilities",
            "energy",
            "water",
        ),
    ),
    (
        "Rent & Mortgage",
        (
            "rent",
            "landlord",
            "letting",
            "letting agent",
            "tenancy",
            "openrent",
            "mortgage",
            "nationwide bs",
            "property management",
            "housing",
            "housing association",
            # Housing associations and lettings firms: "... Homes".
            "homes",
        ),
    ),
    (
        "Subscriptions",
        (
            "netflix",
            "spotify",
            "disney",
            "disney plus",
            "apple com bill",
            "apple services",
            "itunes",
            "google",
            "google one",
            "youtube",
            "amazon prime",
            "prime video",
            "amazon digital",
            "now tv",
            "nowtv",
            "paramount",
            "hayu",
            "britbox",
            "dazn",
            "crunchyroll",
            "deezer",
            "tidal",
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
            "zoom us",
            "zoom video",
            "linkedin",
            "duolingo",
            "strava",
            "patreon",
            "substack",
            "medium",
            "nordvpn",
            "expressvpn",
            "1password",
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
            "steampowered",
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
            "sky bet",
            "bet365",
            "national lottery",
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
            "gym",
            "david lloyd",
            "virgin active",
            "nuffield",
            "anytime fitness",
            "gymshark",
            "myprotein",
            "holland barrett",
            "barber",
            "hairdresser",
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
            "qatar airways",
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
            "car hire",
            "rent a car",
            "enterprise rent",
            "europcar",
            "hertz",
            "avis",
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
            "churchill insurance",
            "hastings direct",
            "lv",
            "esure",
            "quotemehappy",
            "comparethemarket",
            "confused com",
            "gocompare",
        ),
    ),
    ("Tax", ("hmrc", "self assessment")),
    (
        "Education",
        (
            "student loan",
            "slc",
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
            "sadaqah",
        ),
    ),
    (
        "Savings & Investments",
        (
            "trading 212",
            "trading212",
            "vanguard",
            "hargreaves lansdown",
            "aj bell",
            "freetrade",
            "invest",
            "investment",
            "investing",
            "coinbase",
            "kraken",
            "binance",
            "revolut savings",
            "moneybox",
            "plum",
            "chip financial",
            "premium bonds",
            "ns i",
            "nsandi",
            "pension",
            "nutmeg",
            "wealthify",
            "cash isa",
            "stocks and shares isa",
            "lifetime isa",
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

# Words that mean income when money comes in. Paying HMRC is tax, and
# paying for a lesson is education, so these count for money arriving only.
_INCOME_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("Salary", ("salary", "salaries", "payroll", "wages", "bonus", "net pay")),
    (
        "Tutoring",
        (
            "tutor",
            "tutoring",
            "tuition",
            "lesson",
            "maths",
            "math",
            "english",
            "physics",
            "chemistry",
            "biology",
            "science",
            "gcse",
            "a level",
            "11 plus",
            "eleven plus",
            "revision",
            "homework",
        ),
    ),
    (
        "Income",
        (
            "hmrc",
            "dwp",
            "universal credit",
            "child benefit",
            "tax credit",
            "dividend",
            "interest",
            "cashback",
            "stipend",
            "grant",
        ),
    ),
]
# Money back for something you bought: it reduces that spending, it isn't income.
_REFUND_WORDS: tuple[str, ...] = ("refund", "reimbursement", "reversal", "chargeback")


def _words(text: str) -> str:
    """Lower case, punctuation as spaces: the form keywords are written in."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _compile(keywords: list[tuple[str, tuple[str, ...]]]) -> tuple[re.Pattern[str], dict[str, str]]:
    owner: dict[str, str] = {}
    for category, words in keywords:
        for word in words:
            owner.setdefault(_words(word), category)  # the first category listed wins a tie
    # Longest first: where two keywords start at the same word, the longer one matches.
    alternatives = "|".join(re.escape(w) for w in sorted(owner, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?P<kw>{alternatives})(?:e?s)?(?![a-z0-9])"), owner


_SPEND_PATTERN, _SPEND_OWNER = _compile(_KEYWORDS)
_INCOME_PATTERN, _INCOME_OWNER = _compile(_INCOME_KEYWORDS)
_REFUND_PATTERN, _REFUND_OWNER = _compile([("refund", _REFUND_WORDS)])


def _first_keyword(pattern: re.Pattern[str], owner: dict[str, str], words: str) -> str | None:
    match = pattern.search(words)
    return owner[match.group("kw")] if match else None


def keyword_category(text: str) -> str | None:
    """The spending category a merchant name or description points to."""
    return _first_keyword(_SPEND_PATTERN, _SPEND_OWNER, _words(text))


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
    "6513": "Rent & Mortgage",
    "9311": "Tax",
}

# Starling's spendingCategory: every value it sends. You can change these in
# the Starling app, so they carry your own choices too. GENERAL, NONE, OTHER
# and the like say nothing and fall through.
_PROVIDER = {
    "GROCERIES": "Groceries",
    "EATING_OUT": "Eating Out",
    "COFFEE": "Eating Out",
    "DRINKS": "Eating Out",
    "TAKEAWAY": "Eating Out",
    "TRANSPORT": "Transport",
    "TAXI": "Transport",
    "CAR": "Transport",
    "BIKE": "Transport",
    "FUEL": "Fuel",
    "SHOPPING": "Shopping",
    "CLOTHES": "Shopping",
    "EXPENSES": "Shopping",
    "FAMILY": "Shopping",
    "CHILDREN": "Shopping",
    "PETS": "Shopping",
    "HOME": "Shopping",
    "DIY": "Shopping",
    "GARDEN": "Shopping",
    "MAINTENANCE_AND_REPAIRS": "Shopping",
    "TREATS": "Shopping",
    "IMPULSE_BUY": "Shopping",
    "BILLS_AND_SERVICES": "Bills & Utilities",
    "RENT": "Rent & Mortgage",
    "MORTGAGE": "Rent & Mortgage",
    "SUBSCRIPTIONS": "Subscriptions",
    "ENTERTAINMENT": "Entertainment",
    "LIFESTYLE": "Entertainment",
    "GAMING": "Entertainment",
    "HOBBY": "Entertainment",
    "GAMBLING": "Entertainment",
    "PERSONAL_CARE": "Health & Fitness",
    "MEDICAL": "Health & Fitness",
    "FITNESS": "Health & Fitness",
    "WELLBEING": "Health & Fitness",
    # Starling's Holidays and the keywords' Travel are the same money.
    "HOLIDAYS": "Travel",
    "CASH": "Cash",
    "INSURANCE": "Insurance",
    "EDUCATION": "Education",
    "GIFTS": "Gifts & Charity",
    "CHARITY": "Gifts & Charity",
    "CELEBRATION": "Gifts & Charity",
    "WEDDING": "Gifts & Charity",
    "RELATIONSHIPS": "Gifts & Charity",
    "INCOME": "Income",
    "SAVING": "Savings & Investments",
    "INVESTMENTS": "Savings & Investments",
    "DEBT_REPAYMENT": "Transfers",
    "PAYMENTS": "Transfers",
    "TRANSFERS": "Transfers",
}
_CATEGORY_NAMES = {name.lower(): name for name in CATEGORIES}


def provider_category_name(code: str | None) -> str | None:
    """Our category for a provider's: a Starling code, or one of our own
    names (TrueLayer's classification is translated into those)."""
    if not code:
        return None
    code = code.strip()
    # Starling's codes first: its HOLIDAYS is our Travel, not our Holidays.
    mapped = _PROVIDER.get(code) or _CATEGORY_NAMES.get(code.lower())
    return mapped if mapped and mapped != "Uncategorised" else None


def _payment_type_category(payment_type: str | None, amount_minor: int) -> str | None:
    if payment_type == "cash" and amount_minor < 0:
        return "Cash"
    if payment_type == "fee" and amount_minor < 0:
        return "Fees & Interest"
    if payment_type == "interest":
        return "Income" if amount_minor > 0 else "Fees & Interest"
    if payment_type == "dividend" and amount_minor > 0:
        return "Income"
    if payment_type == "internal":
        return "Transfers"
    return None


def categorise(
    *,
    merchant: str,
    description: str,
    amount_minor: int,
    mcc: str | None = None,
    provider_category: str | None = None,
    payment_type: str | None = None,
    rules: list | None = None,
    merchant_key: str | None = None,
) -> tuple[str, str]:
    """Returns (category, source). Pass the stored `merchant_key` for a saved
    transaction, so rules see the key they were made from.

    Money in is income only when it's earned or given. Money back from a
    shop is a refund: it keeps the shop's category, so it reduces that
    spending instead of looking like income."""
    merchant_key = merchant_key or normalise_merchant(merchant or description)
    description_key = (description or "").lower()
    money_in = amount_minor > 0

    # A name in a script other than Latin normalises to nothing; rules then
    # match the name as written.
    rule_merchant = merchant_key or (merchant or description or "").strip().lower()
    for rule in rules or []:
        # A rule that says "this payer is tutoring" is about money they send
        # you, not money you send them.
        if rule.category in INCOME_CATEGORIES and not money_in:
            continue
        target = rule_merchant if rule.field == "merchant" else description_key
        if _rule_matches(rule, target):
            return rule.category, "rule"

    if by_type := _payment_type_category(payment_type, amount_minor):
        return by_type, "auto"

    # The bank's own wording first: "UBER *EATS" says more than the "uber" key.
    words = _words(f"{description_key} {merchant_key}")
    refund = _first_keyword(_REFUND_PATTERN, _REFUND_OWNER, words) is not None
    income = _first_keyword(_INCOME_PATTERN, _INCOME_OWNER, words) if money_in else None
    # "LESSON REFUND" is money back, not tutoring; a tax refund is still income.
    if income and (income == "Income" or not refund):
        return income, "auto"

    spent_on = (
        _first_keyword(_SPEND_PATTERN, _SPEND_OWNER, words)
        or (_MCC.get(str(mcc).zfill(4)) if mcc else None)
        or provider_category_name(provider_category)
    )
    if not money_in:
        return spent_on or "Uncategorised", "auto"

    if spent_on in NOT_SPENDING_OR_INCOME or spent_on in INCOME_CATEGORIES:
        return spent_on, "auto"
    # The bank calls a bank transfer income, and nothing says refund: income,
    # even from a payer whose name is also a shop's (an employer like a supermarket).
    if not refund and payment_type != "card" and provider_category_name(provider_category) == "Income":
        return "Income", "auto"
    refund = refund or payment_type == "card"
    if spent_on:
        return spent_on, "auto"  # money back from somewhere you spend: a refund
    if refund:
        return "Uncategorised", "auto"  # a refund, but of what isn't known
    return "Income", "auto"


def _rule_matches(rule, target: str) -> bool:
    pattern = (rule.pattern or "").lower()
    if not pattern or not target:
        return False
    if rule.match_type == "exact":
        return target.strip() == pattern.strip()
    if rule.match_type == "words":
        # The pattern as whole words: "example homes" finds "example homes
        # flat 2", but "pret" doesn't find "interpret".
        return re.search(rf"(?<![a-z0-9]){re.escape(pattern.strip())}(?![a-z0-9])", target) is not None
    if rule.match_type == "regex":
        try:
            # As written: lower-casing would turn \S into \s.
            return re.search(rule.pattern, target, re.IGNORECASE) is not None
        except re.error:
            return False
    return pattern in target
