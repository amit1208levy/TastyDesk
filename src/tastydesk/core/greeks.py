"""Portfolio greeks in units that mean something across a mixed book.

The number this module replaces
------------------------------
"Net delta: 470 share equivalent" was arrived at by adding up
``delta x contracts x multiplier`` for every leg. That sum is not wrong so much
as meaningless: it adds 300 units of /ZB, where one unit is $1,000 of notional
per point, to 60 units of XLE, where one unit is one $64 share, to 25 units of
/MES at $5 an index point. Three different things share a column and the total
measures none of them. The user asked the obvious question — *delta based on
shares, what does that even mean* — and the honest answer was that it did not
mean anything.

What replaces it
----------------
Two figures a premium seller can act on.

``dollar_delta``
    What the position gains or loses for a 1-point move in *its own*
    underlying, converted to money: ``delta x contracts x multiplier x
    underlying price``. Dollars add up across products; raw deltas do not.

``beta_weighted_delta``
    The same exposure restated in SPY. Each underlying's dollar delta is scaled
    by its beta to SPY (published by tastytrade in market metrics) and divided
    by the SPY price, so the answer is "this book behaves like N shares of SPY".
    That is the number tastytrade's own platform shows, and the only defensible
    way to add a soybean delta to a Best Buy delta.

Theta and vega need no such treatment: multiplied by contracts and multiplier
they are already money — dollars per day, and dollars per one-point move in
implied volatility.

Unknowns stay unknown
---------------------
A missing delta, a missing beta and a missing underlying price each remove that
leg from the affected total, and the leg is named in the result so the page can
say what is not counted. Beta in particular is never defaulted to 1.0: assuming
a soybean contract moves with the S&P (its real beta is 0.07) would overstate
the book's market exposure by more than an order of magnitude, and a confident
wrong number is the one outcome this application exists to avoid.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal

from tastydesk.core.models import ZERO, Leg, Strategy, UnderlyingQuote
from tastydesk.core.occ import product_root

__all__ = [
    "REFERENCE_SYMBOL",
    "GreekTotals",
    "UnderlyingExposure",
    "leg_dollar_delta",
    "portfolio_greeks",
]

# Everything is beta-weighted against this. SPY because that is what tastytrade
# publishes beta against, so the numbers reconcile with the platform.
REFERENCE_SYMBOL = "SPY"


@dataclass(frozen=True, slots=True)
class UnderlyingExposure:
    """One product's contribution to the book's directional risk."""

    product: str
    beta: Decimal | None
    underlying_price: Decimal | None
    # Every contract month held in this product, with its own price. A product
    # traded in two months has two prices and no single one of them is "the"
    # price, so both are reported rather than one being picked or averaged.
    months: tuple[tuple[str, Decimal | None], ...]
    dollar_delta: Decimal | None
    beta_weighted_delta: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    strategies: int
    legs_total: int
    legs_missing_delta: int
    # This product's beta-weighted delta as a share of the book's net. It can
    # exceed 100%: a net of +16 SPY can hide a +37 and a -22 pulling against
    # each other, and that fact matters more than the net does.
    share_of_net: Decimal | None = None


@dataclass(frozen=True, slots=True)
class GreekTotals:
    """The whole book, with what could not be measured named rather than hidden."""

    dollar_delta: Decimal | None
    beta_weighted_dollars: Decimal | None
    beta_weighted_delta: Decimal | None
    theta: Decimal | None
    vega: Decimal | None

    reference_symbol: str = REFERENCE_SYMBOL
    reference_price: Decimal | None = None

    legs_total: int = 0
    legs_with_delta: int = 0
    missing_delta: tuple[str, ...] = ()
    missing_price: tuple[str, ...] = ()
    missing_beta: tuple[str, ...] = ()
    by_underlying: tuple[UnderlyingExposure, ...] = ()

    @property
    def fully_measured(self) -> bool:
        return not (self.missing_delta or self.missing_price or self.missing_beta)

    @property
    def dominant(self) -> UnderlyingExposure | None:
        """The product carrying more than the whole book's net delta, if any.

        A book whose net is small because two large opposite positions cancel is
        not a small book. Naming the largest single contributor is the only way
        the headline stops being misleading.
        """
        loud = [
            e
            for e in self.by_underlying
            if e.share_of_net is not None and abs(e.share_of_net) > 1
        ]
        return max(loud, key=lambda e: abs(e.share_of_net or ZERO)) if loud else None

    @property
    def dollars_per_spy_percent(self) -> Decimal | None:
        """What a 1% move in SPY is worth to this book, in dollars."""
        if self.beta_weighted_dollars is None:
            return None
        return self.beta_weighted_dollars / Decimal(100)


def leg_dollar_delta(leg: Leg, underlying_price: Decimal | None) -> Decimal | None:
    """Money gained per 1-point move in the underlying, signed by direction.

    ``notional_multiplier`` already carries contracts, contract multiplier and
    sign, so this is delta x that x price. Shares have a delta of 1 by
    definition, which is why an assigned lot still counts here.
    """
    if underlying_price is None:
        return None
    if not leg.is_option:
        return leg.notional_multiplier * underlying_price
    if leg.delta is None:
        return None
    return leg.delta * leg.notional_multiplier * underlying_price


def _leg_theta(leg: Leg) -> Decimal | None:
    if not leg.is_option:
        return None
    if leg.theta is None:
        return None
    return leg.theta * leg.notional_multiplier


def _leg_vega(leg: Leg) -> Decimal | None:
    if not leg.is_option:
        return None
    if leg.vega is None:
        return None
    return leg.vega * leg.notional_multiplier


@dataclass
class _Bucket:
    product: str
    beta: Decimal | None = None
    price: Decimal | None = None
    dollar_delta: Decimal = ZERO
    theta: Decimal = ZERO
    vega: Decimal = ZERO
    legs_total: int = 0
    legs_missing_delta: int = 0
    strategies: set[str] = field(default_factory=set)
    priced: bool = True
    prices: set[Decimal] = field(default_factory=set)
    months: dict[str, Decimal | None] = field(default_factory=dict)


def portfolio_greeks(
    strategies: Sequence[Strategy],
    quotes: Mapping[str, UnderlyingQuote],
    *,
    reference_price: Decimal | None = None,
) -> GreekTotals:
    """Aggregate every open leg into dollar and beta-weighted exposure.

    ``quotes`` is keyed however the caller keys it — by contract month, by
    product, or both. Lookups try the exact underlying first and then its
    product root, because beta and volatility belong to /ZB, not to /ZBZ6.
    """
    buckets: dict[str, _Bucket] = {}
    missing_delta: list[str] = []
    missing_price: list[str] = []
    legs_total = 0
    legs_with_delta = 0

    def quote_for(underlying: str) -> UnderlyingQuote | None:
        found = quotes.get(underlying)
        if found is not None:
            return found
        return quotes.get(product_root(underlying))

    def price_for(strategy: Strategy, quote: UnderlyingQuote | None) -> Decimal | None:
        price = (quote.mark or quote.last) if quote else None
        if price is not None:
            return price
        # An outright futures contract is booked against the product — the
        # broker's underlying for it is "/ZB" — and a product has no price of
        # its own. The contract month does, and it is sitting in the leg's own
        # symbol. Without this the whole /ZB bucket had no price, so its delta
        # never reached the beta-weighted total the page leads with.
        for leg in strategy.legs:
            found = quotes.get(leg.symbol)
            month = (found.mark or found.last) if found else None
            if month is not None:
                return month
        return None

    for strategy in strategies:
        quote = quote_for(strategy.underlying)
        price = price_for(strategy, quote)
        product = product_root(strategy.underlying)
        bucket = buckets.setdefault(product, _Bucket(product=product))
        bucket.strategies.add(strategy.id)
        if price is not None:
            # /ZSF7 and /ZSX6 are both /ZS and trade at different prices. Each
            # leg is valued against its own contract month below; the row only
            # shows a price when there is a single one to show.
            bucket.prices.add(price)
        bucket.price = next(iter(bucket.prices)) if len(bucket.prices) == 1 else None
        bucket.months.setdefault(strategy.underlying, price)
        if bucket.beta is None and quote is not None:
            bucket.beta = quote.beta

        if price is None:
            bucket.priced = False
            if strategy.underlying not in missing_price:
                missing_price.append(strategy.underlying)

        for leg in strategy.legs:
            legs_total += 1
            bucket.legs_total += 1

            dollars = leg_dollar_delta(leg, price)
            if dollars is None:
                bucket.legs_missing_delta += 1
                if price is not None:
                    missing_delta.append(leg.symbol)
            else:
                legs_with_delta += 1
                bucket.dollar_delta += dollars

            theta = _leg_theta(leg)
            if theta is not None:
                bucket.theta += theta
            vega = _leg_vega(leg)
            if vega is not None:
                bucket.vega += vega

    if not buckets:
        return GreekTotals(
            dollar_delta=None,
            beta_weighted_dollars=None,
            beta_weighted_delta=None,
            theta=None,
            vega=None,
            reference_price=reference_price,
        )

    dollar_delta = ZERO
    beta_dollars = ZERO
    theta_total = ZERO
    vega_total = ZERO
    missing_beta: list[str] = []
    exposures: list[UnderlyingExposure] = []
    counted_any = False
    counted_any_beta = False

    for product in sorted(buckets):
        bucket = buckets[product]
        # A product with no underlying price contributes nothing anywhere, and
        # its absence is reported rather than absorbed into the total.
        product_delta = bucket.dollar_delta if bucket.priced else None

        if product_delta is not None:
            dollar_delta += product_delta
            counted_any = True

        weighted: Decimal | None = None
        if bucket.beta is None:
            missing_beta.append(product)
        elif product_delta is not None:
            weighted = product_delta * bucket.beta
            beta_dollars += weighted
            counted_any_beta = True

        theta_total += bucket.theta
        vega_total += bucket.vega

        exposures.append(
            UnderlyingExposure(
                product=product,
                beta=bucket.beta,
                underlying_price=bucket.price,
                months=tuple(sorted(bucket.months.items())),
                dollar_delta=product_delta,
                beta_weighted_delta=(
                    None
                    if weighted is None or not reference_price
                    else weighted / reference_price
                ),
                theta=bucket.theta,
                vega=bucket.vega,
                strategies=len(bucket.strategies),
                legs_total=bucket.legs_total,
                legs_missing_delta=bucket.legs_missing_delta,
            )
        )

    weighted_dollars = beta_dollars if counted_any_beta else None

    if weighted_dollars:
        exposures = [
            replace(
                e,
                share_of_net=(
                    None
                    if e.beta_weighted_delta is None or not reference_price
                    else (e.beta_weighted_delta * reference_price) / weighted_dollars
                ),
            )
            for e in exposures
        ]

    return GreekTotals(
        dollar_delta=dollar_delta if counted_any else None,
        beta_weighted_dollars=weighted_dollars,
        beta_weighted_delta=(
            None
            if weighted_dollars is None or not reference_price
            else weighted_dollars / reference_price
        ),
        theta=theta_total,
        vega=vega_total,
        reference_price=reference_price,
        legs_total=legs_total,
        legs_with_delta=legs_with_delta,
        missing_delta=tuple(dict.fromkeys(missing_delta)),
        missing_price=tuple(missing_price),
        missing_beta=tuple(missing_beta),
        by_underlying=tuple(
            sorted(exposures, key=lambda e: abs(e.dollar_delta or ZERO), reverse=True)
        ),
    )
