"""Categories learned from your own choices.

A naive Bayes model, trained on the merchants you've put in a category (by
hand, or with a rule), files money out that nothing else could place. It runs
on the box and keeps nothing: it's trained afresh each time it's used, so it
follows your choices as they change, and nothing is sent anywhere.

What it reads: the words of the merchant's name and the bank's description,
the card's merchant code, and the kind of payment. Each merchant counts once,
however often you pay it, so a shop you use daily doesn't outweigh the rest.

A guess is only used when it's clear:
  - filed, as "learned": at least 90% likely, and two or more merchants you've
    put in that category share a word (or merchant code) with this one;
  - suggested in "Needs a category", for you to confirm: at least 50% likely,
    with one such merchant.
A merchant you've categorised yourself, even one payment of it, is never
guessed for: "just this one" stays just that one.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from .categorise import INCOME_CATEGORIES, _words, normalise_merchant

FILE_AT = 0.9
SUGGEST_AT = 0.5
FILE_SUPPORT = 2  # merchants of the category sharing a word, before it's filed
# Lidstone smoothing: small, so a word seen in one category counts for a lot.
ALPHA = 0.1
# Category sources that are your choices.
CONFIRMED = {"manual", "rule"}
LEARNED = "learned"
UNCATEGORISED = "Uncategorised"
# Words that say nothing about what was bought.
_STOPWORDS = {
    "and",
    "the",
    "for",
    "from",
    "with",
    "www",
    "com",
    "net",
    "org",
    "online",
    "store",
    "shop",
    "shops",
    "services",
    "service",
    "payment",
    "payments",
    "card",
    "transaction",
    "fee",
    "england",
    "united",
    "kingdom",
}


def features(merchant_key: str, description: str, mcc: str | None, payment_type: str | None) -> set[str]:
    words = set(_words(merchant_key).split()) | set(_words(normalise_merchant(description)).split())
    tokens = {w for w in words if len(w) >= 3 and not w.isdigit() and w not in _STOPWORDS}
    if mcc:
        tokens.add(f"mcc:{str(mcc).zfill(4)}")
    if payment_type:
        tokens.add(f"pay:{payment_type}")
    return tokens


def _evidence(tokens: Iterable[str]) -> set[str]:
    """What can tie two merchants together: words and merchant codes, not
    the kind of payment (most things are paid by card)."""
    return {t for t in tokens if not t.startswith("pay:")}


@dataclass(slots=True)
class Guess:
    category: str
    probability: float
    support: int  # merchants in the category sharing evidence with this one
    because: list[str] = field(default_factory=list)  # the shared words

    @property
    def files(self) -> bool:
        return self.probability >= FILE_AT and self.support >= FILE_SUPPORT

    @property
    def suggests(self) -> bool:
        return self.probability >= SUGGEST_AT and self.support >= 1


def _key(txn) -> str:
    """The merchant as the bank names it. A rename can merge several into
    one; each still counts, and is guessed for, on its own."""
    if getattr(txn, "merchant_source", None) and getattr(txn, "bank_merchant_key", None) is not None:
        return txn.bank_merchant_key
    return txn.merchant_key or (txn.merchant or "").strip().lower()


def _learnable(txn) -> bool:
    return txn.amount_minor < 0 and not txn.is_transfer


class Learner:
    """Trained on {merchant key: (category, tokens)}, one per merchant."""

    def __init__(self, examples: dict[str, tuple[str, set[str]]], confirmed_keys: set[str]):
        self.confirmed_keys = confirmed_keys
        self.examples = examples
        self.by_category: dict[str, list[set[str]]] = defaultdict(list)
        for category, tokens in examples.values():
            self.by_category[category].append(tokens)
        self.counts: dict[str, Counter] = {
            c: Counter(t for ts in sets for t in ts) for c, sets in self.by_category.items()
        }
        self.totals = {c: sum(counts.values()) for c, counts in self.counts.items()}
        self.vocabulary = set().union(*(counts.keys() for counts in self.counts.values())) if self.counts else set()

    @classmethod
    def from_rows(cls, rows: Iterable, labels: dict[int, tuple[str, str]] | None = None) -> Learner:
        """`labels` (transaction id -> (category, source)) overrides what's
        stored, for a pass that's working out new categories."""
        votes: dict[str, Counter] = defaultdict(Counter)
        latest: dict[tuple[str, str], object] = {}
        tokens: dict[str, set[str]] = defaultdict(set)
        confirmed: set[str] = set()
        for txn in rows:
            if not _learnable(txn):
                continue
            category, source = (labels or {}).get(txn.id, (txn.category, txn.category_source))
            if source not in CONFIRMED:
                continue
            key = _key(txn)
            confirmed.add(key)
            if category == UNCATEGORISED or category in INCOME_CATEGORIES:
                continue
            votes[key][category] += 1
            pair = (key, category)
            if pair not in latest or txn.booked_on > latest[pair]:
                latest[pair] = txn.booked_on
            tokens[key] |= features(key, txn.description, txn.mcc, txn.payment_type)

        examples = {}
        for key, counts in votes.items():
            # The category used most for this merchant; of two used as often, the latest.
            category = max(counts, key=lambda c: (counts[c], latest[(key, c)]))
            examples[key] = (category, tokens[key])
        return cls(examples, confirmed)

    def guess(self, tokens: set[str]) -> Guess | None:
        known = tokens & self.vocabulary
        if len(self.by_category) < 2 or not _evidence(known):
            return None
        vocabulary = len(self.vocabulary)
        scores = {}
        for category, counts in self.counts.items():
            denominator = self.totals[category] + ALPHA * vocabulary
            scores[category] = sum(math.log((counts[t] + ALPHA) / denominator) for t in known)
        top = max(scores.values())
        weights = {c: math.exp(s - top) for c, s in scores.items()}
        best = max(weights, key=weights.get)
        probability = weights[best] / sum(weights.values())

        evidence = _evidence(known)
        shared = [example & evidence for example in self.by_category[best]]
        support = sum(1 for overlap in shared if overlap)
        because = Counter(t for overlap in shared for t in overlap)
        words = [t for t, _ in because.most_common() if ":" not in t][:3]
        return Guess(best, round(probability, 3), support, words)

    def guess_for(self, txns: list) -> Guess | None:
        """A guess for one merchant's payments, None for a merchant you've
        categorised yourself."""
        if not txns or any(_key(txn) in self.confirmed_keys for txn in txns):
            return None
        tokens: set[str] = set()
        for txn in txns:
            tokens |= features(_key(txn), txn.description, txn.mcc, txn.payment_type)
        return self.guess(tokens)


def fill(rows: list, proposals: dict[int, tuple[str, str]]) -> int:
    """File money out that's still uncategorised, where the guess is clear.
    `proposals` (transaction id -> (category, source)) is changed in place;
    returns how many transactions were filed."""
    learner = Learner.from_rows(rows, proposals)
    if not learner.examples:
        return 0
    waiting: dict[str, list] = defaultdict(list)
    for txn in rows:
        if _learnable(txn) and proposals.get(txn.id) == (UNCATEGORISED, "auto"):
            waiting[_key(txn)].append(txn)

    filed = 0
    for txns in waiting.values():
        guess = learner.guess_for(txns)
        if guess is None or not guess.files:
            continue
        for txn in txns:
            proposals[txn.id] = (guess.category, LEARNED)
        filed += len(txns)
    return filed
