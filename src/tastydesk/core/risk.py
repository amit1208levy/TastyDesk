"""Strategy-level risk scoring.

This module answers one question: *how much attention does this trade need
right now?* It answers it for a :class:`~tastydesk.core.models.Strategy` as a
whole, and never for a single leg.

Why that matters
----------------
A put credit spread down 150% of its credit contains a short put that, on its
own, is down 300%. The 300% is an artefact of looking at half a structure: the
long put gained at the very same moment. Reporting it would hand the user a
panic signal for a position whose true exposure is the width of the spread and
nothing more. So every percentage-based input to the score below is read off
:class:`~tastydesk.core.models.StrategyPnL`, which is already strategy-level.

The only leg-level signals allowed here are *physical* — facts about the
contract rather than about its price:

* a short option in the money, where assignment can actually happen, and
* a short strike the underlying is pinned to on expiry day.

Those are real events with real consequences (shares delivered, buying power
consumed overnight) and they do not cancel out across a structure the way a
mark-to-market percentage does.

Defined risk moderates everything
---------------------------------
A 10-wide put spread at -150% of credit and a naked strangle at -150% of credit
are not the same trade. The spread has a floor; the strangle does not. When the
structure is defined-risk and the loss is still a modest fraction of the most
it can lose, the score is pulled down and a reason says so in plain words. That
single behaviour is the reason this application exists.

Scoring
-------
``score`` is 0-100, purely for sorting the dashboard. It is the sum of the
points each finding contributes, floored at a per-level minimum so a high level
can never sort below a low one, and then multiplied by the defined-risk factor
so that moderation is always *visible* as a lower number. ``level`` is the
worst level among the reasons, which keeps the headline and the list of reasons
telling the same story.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from tastydesk.core.models import (
    ZERO,
    DangerLevel,
    Leg,
    OptionType,
    RiskProfile,
    RiskReason,
    Strategy,
    StrategyPnL,
    StrategyRisk,
    UnderlyingQuote,
)
from tastydesk.core.pnl import covering_legs

__all__ = ["RiskThresholds", "DEFAULT_THRESHOLDS", "assess"]


@dataclass(frozen=True, slots=True)
class RiskThresholds:
    """Every number the scorer judges against, in one tunable place.

    The defaults are this user's own management rules (2x credit stop, 50%
    profit target, 21-DTE line) plus tastytrade's published research on where
    gamma risk starts to bite. Nothing below is a law of nature — pass a
    different instance to :func:`assess` to trade a different style.
    """

    # --- P&L against the credit taken in (StrategyPnL.pct_of_credit) ---
    profit_target_pct: Decimal = Decimal("0.50")
    loss_watch_pct: Decimal = Decimal("-1.0")
    loss_tested_pct: Decimal = Decimal("-1.5")
    loss_danger_pct: Decimal = Decimal("-2.0")

    # --- P&L against the most the structure can lose (defined risk only) ---
    max_loss_tested: Decimal = Decimal("0.50")
    max_loss_danger: Decimal = Decimal("0.75")
    max_loss_critical: Decimal = Decimal("0.90")
    # Below this fraction of max loss, a defined-risk structure is moderated.
    defined_risk_moderate_below: Decimal = Decimal("0.60")
    # How much of the raw score survives moderation.
    defined_risk_score_factor: float = 0.6

    # --- Short strike delta ---
    delta_tested: Decimal = Decimal("0.30")
    delta_danger: Decimal = Decimal("0.45")

    # --- Distance to the nearest short strike, in standard deviations ---
    sigma_watch: Decimal = Decimal("1.5")
    sigma_tested: Decimal = Decimal("1.0")
    sigma_danger: Decimal = Decimal("0.5")

    # --- Time ---
    gamma_dte: int = 21
    expiry_week_dte: int = 7
    assignment_urgent_dte: int = 5
    pin_dte: int = 1
    pin_pct: Decimal = Decimal("0.005")
    days_per_year: Decimal = Decimal("365")

    # --- Concentration against net liq ---
    concentration_watch: Decimal = Decimal("0.05")
    concentration_danger: Decimal = Decimal("0.10")

    # --- Points each finding adds to the 0-100 sort key ---
    points_loss_per_credit: float = 22.0
    points_loss_cap: float = 55.0
    points_max_loss_tested: float = 12.0
    points_max_loss_danger: float = 25.0
    points_delta_tested: float = 12.0
    points_delta_danger: float = 25.0
    points_sigma_watch: float = 6.0
    points_sigma_tested: float = 12.0
    points_sigma_danger: float = 18.0
    points_breach: float = 20.0
    points_breach_both: float = 30.0
    points_dte_gamma: float = 8.0
    points_dte_tested: float = 20.0
    points_assignment: float = 15.0
    points_assignment_urgent: float = 24.0
    points_pin: float = 18.0
    points_concentration_watch: float = 7.0
    points_concentration_danger: float = 15.0
    points_critical: float = 30.0
    # A short call the account's own shares can deliver. Small on purpose: the
    # reading is worth showing, but it must never lift a maxed-out covered call
    # above a trade that is genuinely in trouble on the sort.
    points_covered_call: float = 4.0


DEFAULT_THRESHOLDS = RiskThresholds()

# A level can never sort below the floor of its own severity, however few
# findings produced it. Applied before moderation so that moderation always
# shows up as a strictly smaller number.
_LEVEL_FLOOR: dict[DangerLevel, float] = {
    DangerLevel.OK: 0.0,
    DangerLevel.WATCH: 15.0,
    DangerLevel.TESTED: 35.0,
    DangerLevel.DANGER: 60.0,
    DangerLevel.CRITICAL: 85.0,
}

# One step down the ladder, used when defined risk moderates a loss reading.
_DEMOTE: dict[DangerLevel, DangerLevel] = {
    DangerLevel.CRITICAL: DangerLevel.DANGER,
    DangerLevel.DANGER: DangerLevel.TESTED,
    DangerLevel.TESTED: DangerLevel.WATCH,
    DangerLevel.WATCH: DangerLevel.WATCH,
    DangerLevel.OK: DangerLevel.OK,
}


@dataclass(slots=True)
class _Finding:
    """A reason plus the sort-key points it carries."""

    code: str
    level: DangerLevel
    message: str
    points: float
    # True for readings of unrealised P&L, which a defined structure caps.
    # Physical facts (assignment, pin) are never moderated: a short call goes
    # in the money whether or not you own the wing above it.
    moderatable: bool = False


# --------------------------------------------------------------------------
# formatting helpers — these exist so messages read like a person wrote them
# --------------------------------------------------------------------------


def _num(value: Decimal) -> str:
    """Strike-style number: 580 not 580.00, 4.50 stays 4.5."""
    q = value.normalize()
    if q == q.to_integral_value():
        q = q.to_integral_value()
    return f"{q:f}"


def _money(value: Decimal) -> str:
    return f"${abs(value):,.0f}"


def _pct(fraction: Decimal, places: int = 0) -> str:
    """0.375 -> '38%'. Sign is dropped; the wording carries the direction."""
    scaled = abs(fraction) * 100
    return f"{scaled:.{places}f}%"


def _side(leg: Leg) -> str:
    return "put" if leg.option_type is OptionType.PUT else "call"


def _spot(quote: UnderlyingQuote | None) -> Decimal | None:
    """Last trade if we have one, otherwise the mark. None means no quote."""
    if quote is None:
        return None
    if quote.last is not None:
        return quote.last
    return quote.mark


def _net_shares(strategy: Strategy) -> Decimal:
    """Signed share count held inside this strategy. Long shares are positive."""
    return sum((leg.notional_multiplier for leg in strategy.legs if not leg.is_option), ZERO)


def _covered_short_calls(strategy: Strategy) -> dict[int, str]:
    """The short call legs this strategy's own shares can actually deliver.

    A covered short call is a different animal from a naked one. Naked, the
    loss above the strike is unbounded and assignment means going short stock
    you do not own. Covered, the shares are already sitting there: assignment
    delivers them at a price the holder agreed to when he sold the call, and
    for a covered call being called away IS the maximum profit. Scoring that
    as Danger sorts a finished winner above trades that need the user's hands.

    So this is a *physical* test, on the legs, not on the strategy label — a
    ratio write classified as CUSTOM gets the same treatment as a textbook
    covered call, and a NAKED_CALL with no shares gets none of it.

    Shares are allocated to the lowest strikes first, because those go in the
    money first and get assigned first. Whatever the share count cannot reach
    is genuinely naked and keeps every bit of its alarm; nothing here is a
    blanket suppression.
    """
    covered: dict[int, str] = {}

    available = _net_shares(strategy)
    calls = [leg for leg in strategy.short_legs if leg.option_type is OptionType.CALL]
    for leg in sorted(calls, key=lambda leg: leg.strike if leg.strike is not None else ZERO):
        needed = leg.quantity * leg.multiplier
        if ZERO < needed <= available:
            covered[id(leg)] = "your shares"
            available -= needed

    covered.update(_option_covered_shorts(strategy, OptionType.CALL))
    covered.update(_option_covered_shorts(strategy, OptionType.PUT))
    return covered


def _option_covered_shorts(strategy: Strategy, right: OptionType) -> dict[int, str]:
    """Short options whose loss is capped by a long option in the same strategy.

    Shares are not the only thing that covers a short call. A poor man's covered
    call is a long LEAP under a short front-month call, and the LEAP caps the
    loss exactly as stock would: whatever the underlying does above the short
    strike, the long call gains alongside it. The same holds on the put side for
    the long-dated put sitting under a short one.

    Judged on the legs, never on the label, and only where the long can actually
    do the job:

    * a long call covers a short call struck at or above it;
    * a long put covers a short put struck at or below it;
    * the long must not expire before the short, or the cover is gone while the
      obligation remains.

    Longs are spent on the shorts nearest the money first, since those are the
    ones in trouble first, and each long is used once. A short left over after
    the longs run out is naked and keeps every bit of its alarm.
    """
    kind = "call" if right is OptionType.CALL else "put"
    return {
        short_id: f"your long {cover.strike:g} {kind}"
        for short_id, cover in covering_legs(strategy).items()
        if cover.option_type is right and cover.strike is not None
    }


def _is_itm(leg: Leg, spot: Decimal) -> bool:
    if leg.strike is None:
        return False
    if leg.option_type is OptionType.PUT:
        return spot < leg.strike
    return spot > leg.strike


# --------------------------------------------------------------------------
# the individual checks
# --------------------------------------------------------------------------


def _loss_findings(pnl: StrategyPnL, thresholds: RiskThresholds) -> list[_Finding]:
    """The ladder a premium seller actually manages against."""
    pct = pnl.pct_of_credit

    # The rule is "manage at 50% of MAX PROFIT", so it reads that scale. On a
    # rolled chain the two differ and only this one is reachable: cash paid out
    # to roll is gone, so a trade can sit at 100% of everything still available
    # to it while showing only 29% of the premium it collected over its life.
    # Measured against credit, the nag would never fire on a rolled trade.
    captured = pnl.pct_of_max_profit
    if captured is not None and captured >= thresholds.profit_target_pct:
        # Not danger at all. It is on the list so the dashboard can nag the
        # user to take the trade off, which is its own kind of discipline.
        return [
            _Finding(
                "profit_target",
                DangerLevel.OK,
                f"Up {_pct(captured)} of the most this trade can still make — "
                "at or past your 50% profit target. Take it off.",
                0.0,
            )
        ]

    if pct is None:
        return []

    if pct > ZERO:
        # In profit but not yet at the target: nothing to say, and nothing to score.
        return []

    if pct >= 0:
        return []

    points = min(thresholds.points_loss_cap, thresholds.points_loss_per_credit * float(-pct))

    if pct <= thresholds.loss_danger_pct:
        level = DangerLevel.DANGER
        message = f"Down {_pct(pct)} of the credit you took in — past your 2x stop."
    elif pct <= thresholds.loss_tested_pct:
        level = DangerLevel.TESTED
        message = f"Down {_pct(pct)} of the credit you took in — this one is being tested."
    elif pct <= thresholds.loss_watch_pct:
        level = DangerLevel.WATCH
        message = f"Down {_pct(pct)} of the credit you took in."
    else:
        return []

    return [_Finding("loss_vs_credit", level, message, points, moderatable=True)]


def _max_loss_findings(
    strategy: Strategy, pnl: StrategyPnL, thresholds: RiskThresholds
) -> tuple[list[_Finding], bool]:
    """Read the loss against what the structure can actually lose.

    Returns the findings and whether defined risk should moderate the score.
    """
    if strategy.risk_profile is not RiskProfile.DEFINED:
        return [], False
    if pnl.pct_of_max_loss is None or pnl.max_loss is None:
        return [], False

    used = abs(pnl.pct_of_max_loss)
    worst = _money(pnl.max_loss)
    findings: list[_Finding] = []

    if used >= thresholds.max_loss_danger:
        findings.append(
            _Finding(
                "near_max_loss",
                DangerLevel.DANGER,
                f"You are {_pct(used)} of the way to the {worst} maximum loss on this spread. "
                "The wing is not protecting much any more.",
                thresholds.points_max_loss_danger,
                moderatable=True,
            )
        )
    elif used >= thresholds.max_loss_tested:
        findings.append(
            _Finding(
                "near_max_loss",
                DangerLevel.TESTED,
                f"You are {_pct(used)} of the way to the {worst} maximum loss on this spread.",
                thresholds.points_max_loss_tested,
                moderatable=True,
            )
        )

    moderate = used < thresholds.defined_risk_moderate_below
    if moderate:
        # The whole thesis of the application, said out loud.
        findings.append(
            _Finding(
                "defined_risk",
                DangerLevel.OK,
                f"Risk is defined here. The most this can lose is {worst}, and you are only "
                f"{_pct(used)} of the way there, so the loss against the credit reads far worse "
                "than the actual exposure. Scored down accordingly.",
                0.0,
            )
        )
    return findings, moderate


def _worst_short_option(strategy: Strategy, covered: set[int]) -> Leg | None:
    """The short option leg the market is pressing hardest, by |delta|.

    An uncovered leg always wins the slot over a covered one, even on a smaller
    delta. Otherwise a ratio write — 100 shares against two short calls — would
    report its deep covered call as "the worst" and hide the naked contract
    sitting behind it, which is the only leg in the structure that can run.
    """
    quoted = [leg for leg in strategy.short_legs if leg.delta is not None]
    if not quoted:
        return None
    exposed = [leg for leg in quoted if id(leg) not in covered]
    return max(exposed or quoted, key=lambda leg: abs(leg.delta or ZERO))


def _delta_findings(
    leg: Leg | None, cover: str | None, thresholds: RiskThresholds
) -> tuple[list[_Finding], Decimal | None]:
    if leg is None or leg.delta is None or leg.strike is None:
        return [], None

    worst = abs(leg.delta)
    shown = f"{worst:.2f}"
    strike = _num(leg.strike)

    if worst > thresholds.delta_danger:
        level = DangerLevel.DANGER
        points = thresholds.points_delta_danger
        message = (
            f"Your short {strike} {_side(leg)} is at {shown} delta — that is close to a "
            "coin flip on finishing in the money."
        )
    elif worst > thresholds.delta_tested:
        level = DangerLevel.TESTED
        points = thresholds.points_delta_tested
        message = f"Your short {strike} {_side(leg)} is at {shown} delta — the market is leaning on it."
    else:
        return [], worst

    if cover:
        # Delta on a short option is shorthand for "odds of finishing in the
        # money". On a covered call — covered by shares or by a long call below
        # the strike — that is the odds of being called away at the strike,
        # which is the outcome the trade was opened for. Worth showing, never
        # worth a Danger.
        level = DangerLevel.WATCH
        points = thresholds.points_covered_call
        message = (
            f"Your short {strike} call is at {shown} delta, but {cover} covers it — that is "
            "the chance of being called away, not of a loss."
        )

    return [_Finding("short_delta", level, message, points)], worst


def _nearest_short(strategy: Strategy, spot: Decimal) -> Leg | None:
    candidates = [leg for leg in strategy.short_legs if leg.strike is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda leg: abs((leg.strike or Decimal(0)) - spot))


def _distance_to_short(
    spot: Decimal, strike: Decimal, iv: Decimal | None, dte: int | None, thresholds: RiskThresholds
) -> Decimal | None:
    """Distance to the strike measured in one-standard-deviation units.

    sigma = spot * iv * sqrt(dte / 365), the standard lognormal-ish shorthand
    every options desk uses for an expected move. Returns None rather than a
    guess when the implied vol or the expiry is unknown — a made-up sigma is
    worse than no sigma, because it would be scored.
    """
    sigma = expected_move(spot, iv, dte, thresholds)
    if sigma is None:
        return None
    return (abs(strike - spot) / sigma).quantize(Decimal("0.0001"))


def _rounded(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(Decimal("0.01"))


def expected_move(
    spot: Decimal | None,
    iv: Decimal | None,
    dte: int | None,
    thresholds: RiskThresholds = DEFAULT_THRESHOLDS,
) -> Decimal | None:
    """How far the market is pricing this underlying to move by expiry.

    One standard deviation, in the underlying's own money: price times implied
    volatility times the square root of the time left. The app used to report
    the distance to a short strike as a count of these and call it sigma, which
    is the correct word and told the user nothing. The move itself is a number
    he can picture -- BBY, plus or minus fifteen dollars by January -- and it
    compares across products for exactly the same reason sigma did.
    """
    if spot is None or iv is None or iv <= 0 or dte is None or dte <= 0 or spot <= 0:
        return None
    years = Decimal(dte) / thresholds.days_per_year
    move = spot * iv * Decimal(str(math.sqrt(float(years))))
    return move if move > 0 else None


def position_iv(strategy: Strategy, quote: UnderlyingQuote | None) -> Decimal | None:
    """The volatility this position is actually priced at.

    The product-level index is the wrong number for a futures position and it
    was not close. tastytrade quotes it per product — /ZW, not /ZWZ6 — off
    whatever expiry is nearest, and in a grain that is routinely a different
    animal from the month being held: /ZW read 56% while the December options
    in the account were trading at 28% and 35%. An expected move built on it
    came out at 153 points instead of 87, which turned a strangle with its
    short put 69 points away into a DANGER.

    So the move is priced off the options themselves: the mean implied
    volatility of the option legs at the nearest expiry, which is the market's
    own answer for this contract, this month. The product index remains the
    fallback for a position whose legs the greeks feed has not quoted, and for
    anything with no options in it at all.
    """
    expirations = strategy.expirations
    front = expirations[0] if expirations else None
    ivs = [
        leg.iv
        for leg in strategy.legs
        if leg.is_option and leg.iv is not None and leg.iv > 0 and leg.expiration == front
    ]
    if ivs:
        return sum(ivs, ZERO) / Decimal(len(ivs))
    return quote.iv if quote else None


def _distance_findings(
    strategy: Strategy,
    quote: UnderlyingQuote | None,
    dte: int | None,
    thresholds: RiskThresholds,
    covered: dict[int, str] | None = None,
    iv: Decimal | None = None,
) -> tuple[list[_Finding], Decimal | None, Decimal | None]:
    spot = _spot(quote)
    if spot is None or spot <= 0:
        return [], None, None
    leg = _nearest_short(strategy, spot)
    if leg is None or leg.strike is None:
        return [], None, None

    strike = leg.strike
    distance_pct = (abs(strike - spot) / spot).quantize(Decimal("0.0001"))
    sigma = _distance_to_short(spot, strike, iv, dte, thresholds)
    if sigma is None:
        return [], distance_pct, None

    word = "below" if spot < strike else "above"
    move = expected_move(spot, iv, dte, thresholds)
    # Rounded for the sentence, never before the division above: a soybean move
    # printed to twenty-seven decimal places is not a sentence, and a move
    # rounded before it is divided is not the same number.
    shown = move.quantize(Decimal("0.01")) if move is not None else None
    # The gap in the underlying's own units, because that is what the expected
    # move is in. Saying "21.9% below your strike" and then "a move of about
    # 21.87" invites the reader to compare a percentage with a price and learn
    # nothing from it: the two numbers to hold against each other are 20.28 of
    # room and 21.87 of movement. The percentage stays, in brackets, because it
    # is the figure that compares one position with another.
    gap = (abs(strike - spot)).quantize(Decimal("0.01"))
    where = (
        f"{strategy.underlying} at {_num(spot)} is {_num(gap)} {word} your "
        f"{_num(strike)} short {_side(leg)} ({_pct(distance_pct, 1)} away)"
    )
    # The same fact the sigma count carried, in something the reader can
    # picture: what the market says this thing moves in the time left.
    priced = f"the market is pricing a move of about {_num(shown)} by expiry" if shown else ""

    # A covered short has cover by definition, so "barely half a standard
    # deviation of cover left" was the app arguing with itself: the line under
    # it said the long call covers this one. Approaching a covered strike is
    # not danger, it is an outcome -- the position is heading for assignment at
    # a price already agreed -- so it is reported as that and nothing more.
    cover = (covered or {}).get(id(leg))
    if cover is not None and sigma <= thresholds.sigma_tested:
        return (
            [
                _Finding(
                    "distance_to_short",
                    DangerLevel.WATCH,
                    f"{where}, and {priced}. {cover.capitalize()} covers it, so this is the "
                    f"market walking towards your {_num(strike)} exit, not towards a loss.",
                    thresholds.points_sigma_watch,
                )
            ],
            distance_pct,
            sigma,
        )

    if sigma <= thresholds.sigma_danger:
        finding = _Finding(
            "distance_to_short",
            DangerLevel.DANGER,
            f"{where} — {priced}, several times the distance left to it.",
            thresholds.points_sigma_danger,
        )
    elif sigma <= thresholds.sigma_tested:
        finding = _Finding(
            "distance_to_short",
            DangerLevel.TESTED,
            f"{where} — {priced}, which more than covers the distance left to it.",
            thresholds.points_sigma_tested,
        )
    elif sigma <= thresholds.sigma_watch:
        finding = _Finding(
            "distance_to_short",
            DangerLevel.WATCH,
            f"{where} — {priced}, which just about reaches it.",
            thresholds.points_sigma_watch,
        )
    else:
        return [], distance_pct, sigma

    return [finding], distance_pct, sigma


def _breach_findings(
    strategy: Strategy,
    quote: UnderlyingQuote | None,
    covered: set[int],
    thresholds: RiskThresholds,
) -> tuple[list[_Finding], str | None, bool]:
    """Has the underlying actually traded through a short strike?

    Returns the findings, which side broke, and whether the only thing broken
    is a short call the shares already cover — in which case nothing is under
    pressure and the caller must not escalate on it.
    """
    spot = _spot(quote)
    if spot is None:
        return [], None, False

    breached_put: Leg | None = None
    breached_calls: list[Leg] = []
    for leg in strategy.short_legs:
        if leg.strike is None:
            continue
        if leg.option_type is OptionType.PUT and spot < leg.strike:
            if breached_put is None or leg.strike > (breached_put.strike or Decimal(0)):
                breached_put = leg
        elif leg.option_type is OptionType.CALL and spot > leg.strike:
            breached_calls.append(leg)

    # Report the lowest breached call, but an uncovered one first: with two
    # short calls through the money and shares for only one of them, the naked
    # contract is the whole story and must not be hidden behind the covered one.
    breached_calls.sort(key=lambda leg: (id(leg) in covered, leg.strike or ZERO))
    breached_call = breached_calls[0] if breached_calls else None

    # A breached short call whose shares are already in the account is the
    # trade working, not the trade breaking. Only when it is the *only* thing
    # breached, though: if a short put has gone through as well, that side has
    # no shares behind it and the pair still reads Danger.
    if breached_call is not None and breached_put is None and id(breached_call) in covered:
        strike = _num(breached_call.strike or ZERO)
        return (
            [
                _Finding(
                    "breached",
                    DangerLevel.WATCH,
                    f"{strategy.underlying} at {_num(spot)} is above your {strike} short call, but "
                    f"{covered[id(breached_call)]} covers it — you are on track to be called "
                    f"away at {strike}.",
                    thresholds.points_covered_call,
                )
            ],
            "call",
            True,
        )

    if breached_put is not None and breached_call is not None:
        side = "both"
        message = (
            f"{strategy.underlying} at {_num(spot)} has traded through both short strikes "
            f"({_num(breached_put.strike or Decimal(0))} put and "
            f"{_num(breached_call.strike or Decimal(0))} call)."
        )
        points = thresholds.points_breach_both
    elif breached_put is not None:
        side = "put"
        message = (
            f"{strategy.underlying} at {_num(spot)} is through your "
            f"{_num(breached_put.strike or Decimal(0))} short put."
        )
        points = thresholds.points_breach
    elif breached_call is not None:
        side = "call"
        message = (
            f"{strategy.underlying} at {_num(spot)} is through your "
            f"{_num(breached_call.strike or Decimal(0))} short call."
        )
        points = thresholds.points_breach
    else:
        return [], None, False

    return [_Finding("breached", DangerLevel.DANGER, message, points)], side, False


def _dte_findings(
    dte: int | None,
    tested: bool,
    thresholds: RiskThresholds,
    *,
    opened_inside: bool = False,
) -> list[_Finding]:
    """The 21-day line, and the last week with a short strike under pressure.

    Gamma is gamma whenever you bought it, so a short-dated trade still earns
    the warning. What it does not earn is the words "past your line": the line
    is a rule about when to leave, and a trade sold with eight days on it was
    never on the other side of it.
    """
    if dte is None or dte < 0:
        return []

    if dte <= thresholds.expiry_week_dte and tested:
        return [
            _Finding(
                "expiry_week",
                DangerLevel.DANGER,
                f"{dte} days to expiry with a short strike under pressure. Gamma moves the "
                "position faster than you can react now.",
                thresholds.points_dte_tested,
            )
        ]
    if dte <= thresholds.gamma_dte:
        wording = (
            f"{dte} days to expiry. You sold it short-dated, so this is not past your line, "
            "but gamma still moves it faster than the premium left pays for."
            if opened_inside
            else f"{dte} days to expiry — past your {thresholds.gamma_dte}-day line, where gamma "
            "risk climbs and the remaining premium stops paying for it."
        )
        return [
            _Finding("gamma_window", DangerLevel.WATCH, wording, thresholds.points_dte_gamma)
        ]
    return []


def _concentration_findings(
    strategy: Strategy, net_liq: Decimal | None, thresholds: RiskThresholds
) -> tuple[list[_Finding], Decimal | None]:
    bpu = strategy.buying_power_used
    if bpu is None or net_liq is None or net_liq <= 0:
        return [], None

    share = (abs(bpu) / net_liq).quantize(Decimal("0.0001"))
    if share >= thresholds.concentration_danger:
        return (
            [
                _Finding(
                    "concentration",
                    DangerLevel.DANGER,
                    f"This one trade is holding {_pct(share, 1)} of your net liq. One position "
                    "should not be able to set your month.",
                    thresholds.points_concentration_danger,
                )
            ],
            share,
        )
    if share >= thresholds.concentration_watch:
        return (
            [
                _Finding(
                    "concentration",
                    DangerLevel.WATCH,
                    f"This trade is holding {_pct(share, 1)} of your net liq — a big single bet.",
                    thresholds.points_concentration_watch,
                )
            ],
            share,
        )
    return [], share


def _assignment_findings(
    strategy: Strategy,
    quote: UnderlyingQuote | None,
    covered: set[int],
    today: date,
    thresholds: RiskThresholds,
) -> tuple[list[_Finding], bool]:
    """Physical alarm: a short option that can actually be exercised against you.

    This is leg-level on purpose and it is legitimate, because assignment is an
    event, not a mark. Nothing about a long wing stops the short leg's shares
    from showing up in the account.
    """
    spot = _spot(quote)
    if spot is None:
        return [], False

    findings: list[_Finding] = []
    ex_div = quote.ex_dividend_date if quote else None

    for leg in strategy.short_legs:
        if leg.strike is None or not _is_itm(leg, spot):
            continue
        leg_dte = leg.dte(today)
        strike = _num(leg.strike)
        left = f"{leg_dte} days left" if leg_dte is not None else "no expiry on file"

        # A short call goes early when the dividend is worth more than the
        # remaining extrinsic value — the textbook early-exercise case.
        dividend_at_risk = (
            leg.option_type is OptionType.CALL
            and ex_div is not None
            and ex_div >= today
            and (leg.expiration is None or ex_div <= leg.expiration)
        )

        if id(leg) in covered:
            # Assignment here is not a threat, it is the exit. The shares are
            # already in the account, and the call-away price is the strike the
            # holder chose. Say what will happen instead of raising an alarm.
            note = ""
            if dividend_at_risk:
                # Still worth a word: early assignment costs him the dividend
                # he would otherwise have collected on the shares.
                note = (
                    f" {strategy.underlying} goes ex-dividend on {ex_div:%d %b}, so it may happen "
                    "early"
                    + (
                        " and you would miss the dividend."
                        if covered[id(leg)] == "your shares"
                        else "."
                    )
                )
            findings.append(
                _Finding(
                    f"assignment_covered:{leg.symbol}",
                    DangerLevel.WATCH,
                    f"Your short {strike} call is in the money with {left}. You will be called "
                    f"away at {strike}, which is this trade's maximum profit — "
                    f"{covered[id(leg)]} is already there to cover it.{note}",
                    thresholds.points_covered_call,
                )
            )
        elif dividend_at_risk:
            findings.append(
                _Finding(
                    f"assignment_dividend:{leg.symbol}",
                    DangerLevel.DANGER,
                    f"Your short {strike} call is in the money and {strategy.underlying} goes "
                    f"ex-dividend on {ex_div:%d %b}, before expiry. Expect early assignment from "
                    "someone reaching for the dividend.",
                    thresholds.points_assignment_urgent,
                )
            )
        elif leg_dte is not None and leg_dte <= thresholds.assignment_urgent_dte:
            findings.append(
                _Finding(
                    f"assignment:{leg.symbol}",
                    DangerLevel.DANGER,
                    f"Your short {strike} {_side(leg)} is in the money with {left}. Assignment is "
                    "live — decide whether you want the shares before the market does.",
                    thresholds.points_assignment_urgent,
                )
            )
        else:
            findings.append(
                _Finding(
                    f"assignment:{leg.symbol}",
                    DangerLevel.TESTED,
                    f"Your short {strike} {_side(leg)} is in the money with {left}.",
                    thresholds.points_assignment,
                )
            )

    return findings, bool(findings)


def _pin_findings(
    strategy: Strategy,
    quote: UnderlyingQuote | None,
    today: date,
    thresholds: RiskThresholds,
) -> tuple[list[_Finding], bool]:
    """Physical alarm: expiry day, price sitting on the strike.

    Pin risk is nasty because you find out after the close whether you were
    assigned, and you carry the share position over the weekend either way.
    """
    spot = _spot(quote)
    if spot is None or spot <= 0:
        return [], False

    for leg in strategy.short_legs:
        if leg.strike is None:
            continue
        leg_dte = leg.dte(today)
        if leg_dte is None or leg_dte > thresholds.pin_dte or leg_dte < 0:
            continue
        if abs(leg.strike - spot) / spot <= thresholds.pin_pct:
            day_word = "today" if leg_dte == 0 else "tomorrow"
            return (
                [
                    _Finding(
                        "pin_risk",
                        DangerLevel.DANGER,
                        f"{strategy.underlying} at {_num(spot)} is sitting right on your "
                        f"{_num(leg.strike)} short {_side(leg)} and it expires {day_word}. "
                        "You will not know if you were assigned until after the close.",
                        thresholds.points_pin,
                    )
                ],
                True,
            )
    return [], False


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------


def assess(
    strategy: Strategy,
    pnl: StrategyPnL,
    quote: UnderlyingQuote | None,
    today: date,
    net_liq: Decimal | None = None,
    *,
    thresholds: RiskThresholds = DEFAULT_THRESHOLDS,
) -> StrategyRisk:
    """Score one strategy's risk. Strategy level only — see the module docstring."""
    dte = strategy.dte(today)

    # Which short calls the account's own shares can deliver. Computed once and
    # threaded through, because the same fact changes three separate readings.
    covered = _covered_short_calls(strategy)

    loss = _loss_findings(pnl, thresholds)
    max_loss, moderate = _max_loss_findings(strategy, pnl, thresholds)
    worst_leg = _worst_short_option(strategy, covered)
    delta, worst_delta = _delta_findings(
        worst_leg, covered.get(id(worst_leg)) if worst_leg is not None else None, thresholds
    )
    # What this position is priced at, which for a futures month is not what
    # the product index says. Used for the expected move and for every reading
    # built on it.
    priced_iv = position_iv(strategy, quote)
    distance, distance_pct, sigma = _distance_findings(
        strategy, quote, dte, thresholds, covered, iv=priced_iv
    )
    breach, breached_side, breach_covered = _breach_findings(strategy, quote, covered, thresholds)
    assignment, assignment_risk = _assignment_findings(strategy, quote, covered, today, thresholds)
    pin, pin_risk = _pin_findings(strategy, quote, today, thresholds)
    concentration, pct_of_net_liq = _concentration_findings(strategy, net_liq, thresholds)

    # "Tested" for the expiry-week rule means the market is at or through the
    # short strike, not that the position shows a loss.
    #
    # When every short option in the strategy is share-covered there is no
    # gamma emergency to have: expiry resolves into a delivery of shares that
    # are already sitting in the account. Escalating a covered call to Danger
    # in its last week would undo the whole point of treating it as covered,
    # so the pressure test skips it. One uncovered short leg anywhere and the
    # normal rule is back.
    all_shorts_covered = bool(strategy.short_legs) and all(id(leg) in covered for leg in strategy.short_legs)
    tested = not all_shorts_covered and (
        (breached_side is not None and not breach_covered)
        or (worst_delta is not None and worst_delta > thresholds.delta_tested)
        or (sigma is not None and sigma <= thresholds.sigma_tested)
    )
    entry_dte = strategy.front_entry_dte
    dte_findings = _dte_findings(
        dte,
        tested,
        thresholds,
        opened_inside=entry_dte is not None and entry_dte <= thresholds.gamma_dte,
    )

    findings = [
        *loss,
        *max_loss,
        *delta,
        *distance,
        *breach,
        *dte_findings,
        *assignment,
        *pin,
        *concentration,
    ]

    # Escalation to CRITICAL. An undefined structure with three independent
    # danger signals has no floor under it; a defined one only earns CRITICAL
    # by actually approaching its maximum loss.
    danger_count = sum(1 for f in findings if f.level is DangerLevel.DANGER)
    used_of_max = abs(pnl.pct_of_max_loss) if pnl.pct_of_max_loss is not None else None
    if used_of_max is not None and used_of_max >= thresholds.max_loss_critical:
        findings.append(
            _Finding(
                "critical",
                DangerLevel.CRITICAL,
                f"This structure is at {_pct(used_of_max)} of everything it can lose. There is "
                "nothing left to defend.",
                thresholds.points_critical,
            )
        )
    elif not moderate and danger_count >= 3 and strategy.risk_profile is RiskProfile.UNDEFINED:
        findings.append(
            _Finding(
                "critical",
                DangerLevel.CRITICAL,
                "Several danger signals at once on a position with no defined loss. This is the "
                "shape of the trade that does real damage — deal with it first.",
                thresholds.points_critical,
            )
        )

    if pnl.total_legs and not pnl.fully_quoted:
        findings.append(
            _Finding(
                "partial_quotes",
                DangerLevel.OK,
                f"Only {pnl.quoted_legs} of {pnl.total_legs} legs are quoted, so these numbers "
                "are incomplete.",
                0.0,
            )
        )

    raw = sum(f.points for f in findings)
    pre_level = _worst_level(findings)
    raw = max(raw, _LEVEL_FLOOR[pre_level])

    if moderate:
        # Moderation does two things: it demotes the *mark-to-market* readings
        # one step (physical alarms are untouched) and it scales the sort key
        # down, so a capped-loss trade never outranks an uncapped one that is
        # in identical trouble.
        findings = [
            _Finding(f.code, _DEMOTE[f.level], f.message, f.points, f.moderatable) if f.moderatable else f
            for f in findings
        ]
        raw *= thresholds.defined_risk_score_factor

    level = _worst_level(findings)
    score = round(min(100.0, max(0.0, raw)), 1)

    reasons = [RiskReason(f.code, f.level, f.message) for f in sorted(findings, key=lambda f: -f.level.rank)]

    return StrategyRisk(
        level=level,
        score=score,
        reasons=reasons,
        dte=dte,
        worst_short_delta=worst_delta,
        distance_to_short_pct=distance_pct,
        short_strike_in_moves=sigma,
        expected_move=_rounded(expected_move(_spot(quote), priced_iv, dte, thresholds)),
        breached=breached_side is not None,
        breached_side=breached_side,
        assignment_risk=assignment_risk,
        pin_risk=pin_risk,
        pct_of_net_liq=pct_of_net_liq,
    )


def _worst_level(findings: list[_Finding]) -> DangerLevel:
    level = DangerLevel.OK
    for finding in findings:
        if finding.level.rank > level.rank:
            level = finding.level
    return level
