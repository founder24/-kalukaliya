#!/usr/bin/env python3
"""
Full AHSEC Arts Content Seeder
Seeds AHSEC HS 1st Year Arts stream with complete syllabus chapters + topic-wise notes.

Subjects covered:
  - Economics (Part A: Microeconomics + Part B: Statistics for Economics)
  - Political Science
  - History

All chapters are seeded with:
  - Full topic lists (published_topics)
  - Rich topic-wise markdown notes (content_en)
  - status = "published"

Usage:
  export MONGODB_URI="mongodb+srv://..."
  python seed-ahsec-arts-full.py

  # Or pass URI directly:
  python seed-ahsec-arts-full.py --mongodb-uri "mongodb+srv://..."

  # Dry-run preview:
  python seed-ahsec-arts-full.py --dry-run

  # Only one subject:
  python seed-ahsec-arts-full.py --subject Economics
"""

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timezone
from uuid import uuid4

from pymongo import MongoClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DB_NAME = "syrabit_prod"

# ─── Helpers ──────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def now_utc():
    return datetime.now(timezone.utc)


def make_topic(title: str) -> dict:
    return {
        "id": str(uuid4()),
        "title": title,
        "definition": None,
        "topic_slug": slugify(title),
        "definition_status": "pending",
    }


# ─── Economics HS 1st Year – Full AHSEC Syllabus ─────────────────────────────
# Based on official AHSEC HS First Year Economics syllabus
# Part A: Introductory Microeconomics | Part B: Statistics for Economics

ECONOMICS_CHAPTERS = [
    # ── PART A: INTRODUCTORY MICROECONOMICS ──────────────────────────────────

    # UNIT I
    (
        "Introduction to Economics",
        [
            "What is Economics",
            "Microeconomics",
            "Macroeconomics",
            "Positive Economics",
            "Normative Economics",
            "Central Problems of an Economy",
            "What is an Economy",
            "Production Possibility Frontier (PPF)",
            "Opportunity Cost",
        ],
        """# Introduction to Economics

## What is Economics

Economics is a social science that studies how individuals, businesses, governments, and societies make choices about allocating scarce resources to satisfy unlimited wants. It examines production, distribution, and consumption of goods and services.

**Key features of Economics:**
- Studies human behaviour in relation to scarce resources
- Analyses how choices are made under constraints
- Covers both individual decisions (micro) and economy-wide decisions (macro)

## Microeconomics

Microeconomics is the branch of economics that studies the behaviour of individual economic units such as consumers, firms, and industries. It focuses on:

- How individual consumers decide what to buy
- How firms decide what to produce and at what price
- How prices are determined in individual markets
- How markets allocate resources

**Examples:** Demand for a product, pricing strategy of a firm, consumer choice.

## Macroeconomics

Macroeconomics studies the economy as a whole. It deals with aggregate economic phenomena such as:

- National income and output (GDP)
- Employment and unemployment
- General price level and inflation
- Money supply and monetary policy
- International trade and balance of payments

**Examples:** India's GDP growth, inflation rate, national employment level.

## Positive Economics

Positive economics deals with **what is** — it describes and explains economic phenomena as they exist, without making value judgements.

- Based on facts and empirical evidence
- Can be tested and verified
- Examples: "When income rises, consumption rises" or "A fall in price leads to higher demand"

## Normative Economics

Normative economics deals with **what ought to be** — it involves value judgements about what is desirable or undesirable in economic policy.

- Based on opinions and values
- Cannot be tested or verified scientifically
- Examples: "The government should reduce income inequality" or "The minimum wage should be raised"

## Central Problems of an Economy

Every economy faces three fundamental (central) problems due to scarcity of resources:

1. **What to produce?** — Which goods and services should be produced and in what quantities?
2. **How to produce?** — Which production technique should be used (labour-intensive or capital-intensive)?
3. **For whom to produce?** — Who will get the goods produced? How will national output be distributed?

These problems arise because resources (land, labour, capital) are limited while human wants are unlimited.

## What is an Economy

An economy is a system by which people organise their material existence. It is a mechanism that allocates scarce resources among competing uses. Types of economies:

- **Capitalist Economy:** Resources owned privately; market forces determine allocation
- **Socialist Economy:** Resources owned by the state; government plans allocation
- **Mixed Economy:** Both private and public sectors coexist (e.g., India)

## Production Possibility Frontier (PPF)

The Production Possibility Frontier (PPF) is a curve that shows all possible combinations of two goods that can be produced with the available resources and technology, when resources are fully and efficiently employed.

**Key features of PPF:**
- **Downward sloping:** To produce more of one good, some of the other must be sacrificed
- **Concave to origin:** Due to increasing opportunity cost
- **Points on the curve:** Efficient production (full employment of resources)
- **Points inside the curve:** Inefficient production (underemployment)
- **Points outside the curve:** Not attainable with current resources

**Shifts in PPF:**
- **Outward shift:** Economic growth (more resources or better technology)
- **Inward shift:** Destruction of resources or fall in technology

## Opportunity Cost

Opportunity cost is the cost of the next best alternative foregone when a choice is made. It is the value of what you give up to get something.

**Formula:** Opportunity Cost = Value of next best alternative sacrificed

**Example:** If a student decides to study instead of working a part-time job that pays ₹500, the opportunity cost is ₹500.

**Importance:**
- Forms the basis of PPF analysis
- Explains why PPF is concave (increasing opportunity cost)
- Helps individuals and firms make rational decisions

---

*Note: These are comprehensive notes for AHSEC HS 1st Year Economics. Refer to NCERT Class 11 Economics textbook for additional examples and exercises.*
""",
    ),

    # UNIT II – Consumer's Equilibrium
    (
        "Consumer's Equilibrium – Utility Analysis",
        [
            "Utility",
            "Total Utility",
            "Marginal Utility",
            "Law of Diminishing Marginal Utility",
            "Consumer's Equilibrium",
        ],
        """# Consumer's Equilibrium – Utility Analysis

## Utility

Utility is the want-satisfying power of a commodity. It is the satisfaction a consumer gets from consuming a good or service. Utility is subjective — it varies from person to person and from time to time.

**Types of Utility:**
- **Form Utility:** Created by changing the form of a material (e.g., wood → furniture)
- **Place Utility:** Created by transporting goods to where they are needed
- **Time Utility:** Created by storing goods and making them available when needed

**Note:** Utility has no ethical or moral dimension. Even harmful goods (e.g., alcohol) may have utility if they satisfy a want.

## Total Utility (TU)

Total Utility is the aggregate satisfaction obtained from consuming all units of a commodity up to a given point.

**TU = Sum of marginal utilities of all units consumed**

| Units Consumed | Marginal Utility | Total Utility |
|---|---|---|
| 1 | 10 | 10 |
| 2 | 8 | 18 |
| 3 | 6 | 24 |
| 4 | 4 | 28 |
| 5 | 2 | 30 |
| 6 | 0 | 30 |
| 7 | -2 | 28 |

Total utility rises at a diminishing rate, reaches a maximum, and then falls.

## Marginal Utility (MU)

Marginal Utility is the additional utility gained from consuming one more unit of a good.

**MU = TUn – TU(n-1)**

Or: **MU = ΔTU / ΔQ**

**Relationship between TU and MU:**
- When MU is positive → TU is rising
- When MU = 0 → TU is maximum (Point of Satiation)
- When MU is negative → TU is falling

## Law of Diminishing Marginal Utility

The Law of Diminishing Marginal Utility (DMU) states that as a consumer consumes more and more units of a commodity, the marginal utility derived from each successive unit goes on decreasing.

**Assumptions:**
1. The consumer is rational
2. Units consumed are homogeneous (same in all respects)
3. Consumption is continuous
4. Consumer's income, tastes, and prices remain constant

**Exceptions (Law does not apply):**
- Collecting hobbies (stamps, coins)
- Alcohol or addiction
- Knowledge and money (sometimes debated)

## Consumer's Equilibrium (Single Commodity)

A consumer is in equilibrium when they maximise their utility subject to their budget constraint.

**Condition for equilibrium with one good:**
> A consumer buying a single commodity will be at equilibrium when:
> **MU = Price (P)**

- If MU > P → consumer buys more (increases utility)
- If MU < P → consumer buys less (reduces loss)
- If MU = P → equilibrium (maximum utility achieved)

**In terms of money:** MU of good (in utils) / Marginal Utility of Money = Price

Since MU of money is assumed to be constant (= 1 util per rupee in cardinal analysis), equilibrium occurs when **MUx = Px**.

---

*These notes follow the AHSEC HS 1st Year Economics syllabus (NCERT Class 11).*
""",
    ),

    (
        "Consumer's Equilibrium – Indifference Curve Analysis",
        [
            "Indifference Curve",
            "Indifference Map",
            "Budget Set",
            "Budget Line",
            "Conditions of Consumer's Equilibrium",
        ],
        """# Consumer's Equilibrium – Indifference Curve Analysis

## Indifference Curve

An Indifference Curve (IC) is a curve that shows all possible combinations of two goods that give the consumer the same level of satisfaction (utility). The consumer is indifferent between all points on the curve.

**Properties of Indifference Curves:**
1. **Downward sloping:** To get more of one good, you must give up some of the other
2. **Convex to origin:** Due to diminishing Marginal Rate of Substitution (MRS)
3. **Higher IC = Higher satisfaction:** Curves further from origin represent greater utility
4. **ICs never intersect:** Intersection would lead to a logical contradiction

**Marginal Rate of Substitution (MRS):**
MRS is the rate at which a consumer is willing to exchange one good for another, keeping satisfaction constant.

**MRS = ΔY / ΔX** (Amount of Y sacrificed per unit of X gained)

MRS diminishes as we move down the IC → hence ICs are convex.

## Indifference Map

An Indifference Map is a set of indifference curves for a consumer. Higher curves in the map represent higher levels of satisfaction.

- IC₁ < IC₂ < IC₃ (in terms of satisfaction)
- A consumer always prefers to be on the highest possible IC

## Budget Set

The budget set is the set of all combinations of two goods that a consumer can afford, given their income and the prices of the goods.

**Budget Set:** {(X, Y) : Px·X + Py·Y ≤ M}

Where:
- Px, Py = prices of goods X and Y
- M = consumer's income

All bundles on or below the budget line are affordable.

## Budget Line

The Budget Line (Budget Constraint) shows all combinations of two goods that a consumer can buy by spending their entire income.

**Equation of Budget Line:** Px·X + Py·Y = M

**Slope of Budget Line:** = –Px/Py (negative, showing trade-off)

**X-intercept:** M/Px (maximum X if all income spent on X)
**Y-intercept:** M/Py (maximum Y if all income spent on Y)

**Shifts in Budget Line:**
- **Income increases → Budget line shifts outward (parallel)**
- **Price of X falls → Budget line rotates outward on X-axis**
- **Price of Y rises → Budget line rotates inward on Y-axis**

## Conditions of Consumer's Equilibrium (IC Analysis)

The consumer achieves equilibrium where the budget line is tangent to the highest possible indifference curve.

**Two conditions:**

**1. Necessary Condition:**
**MRS(xy) = Px/Py**

The slope of the IC = Slope of the budget line.

**2. Sufficient Condition:**
The indifference curve must be convex to origin at the point of equilibrium.

**Explanation:**
- If MRS > Px/Py → consumer can gain utility by buying more X → not at equilibrium
- If MRS < Px/Py → consumer can gain by buying more Y → not at equilibrium
- If MRS = Px/Py and IC is convex → consumer is at equilibrium (maximum utility)

---

*AHSEC HS 1st Year Economics – Indifference Curve Analysis based on NCERT Class 11 textbook.*
""",
    ),

    (
        "Demand",
        [
            "Meaning of Demand",
            "Law of Demand",
            "Demand Curve",
            "Derivation of Demand Curve",
            "Budget Constraints",
            "Normal Goods",
            "Inferior Goods",
            "Giffen Goods",
            "Market Demand",
            "Movement Along Demand Curve",
            "Shift in Demand Curve",
            "Price Elasticity of Demand",
            "Measurement of Elasticity",
            "Percentage Method",
            "Geometric Method",
            "Factors Affecting Elasticity",
            "Elasticity and Expenditure",
        ],
        """# Demand

## Meaning of Demand

Demand refers to the quantity of a good or service that a consumer is willing and able to buy at a given price, during a given period of time. Demand requires:
1. **Desire** for the good
2. **Willingness** to pay
3. **Ability** to pay (purchasing power)

**Demand Function:** Dx = f(Px, Py, M, T, ...) where Px = own price, Py = price of related goods, M = income, T = tastes.

## Law of Demand

The Law of Demand states that, other things being equal (ceteris paribus), as the price of a good rises, its quantity demanded falls, and as price falls, quantity demanded rises.

**Inverse relationship between price and quantity demanded.**

**Reasons for Law of Demand:**
1. **Substitution Effect:** When price of X rises, substitute goods become relatively cheaper → consumers substitute away from X
2. **Income Effect:** A rise in price reduces real income → consumers buy less of X
3. **New consumers:** Lower price attracts new buyers into the market

**Exceptions to Law of Demand (Demand curves that slope upward):**
- Giffen goods
- Articles of snob appeal / Veblen goods
- Expectations of price rise
- Necessities of life

## Demand Curve

The demand curve is a graphical representation of the relationship between price and quantity demanded, holding other factors constant. It slopes downward from left to right.

## Derivation of Demand Curve

From the consumer's equilibrium (IC analysis), we can derive the demand curve:

1. Start at equilibrium point where budget line is tangent to IC
2. Change the price of good X (while keeping income and Py constant)
3. Plot the new equilibrium point
4. Join all price-quantity combinations → **Price Consumption Curve (PCC)**
5. Plot the PCC information on a price-quantity graph → **Demand Curve**

## Normal Goods

Normal goods are goods for which demand increases when income increases (and vice versa). Income Effect is positive.

**Examples:** Branded clothes, restaurant meals, electronics.

**Income Elasticity of Demand > 0** for normal goods.

## Inferior Goods

Inferior goods are goods for which demand decreases when income increases. As income rises, consumers switch to better (superior) goods.

**Examples:** Coarse grain, low-quality public transport.

**Income Elasticity of Demand < 0** for inferior goods.

## Giffen Goods

Giffen goods are a special type of inferior good for which the demand curve slopes upward — as price rises, quantity demanded also rises. Named after Sir Robert Giffen.

**Condition:** The income effect (negative) must outweigh the substitution effect.

**Classic Example:** Bread (staple food of the poor) — when bread price rises, real income falls so much that people cannot afford superior goods, and are forced to buy even more bread.

## Market Demand

Market demand is the sum of individual demands for a good at each price level.

**Market Demand = ΣIndividual Demands**

If consumer A demands 10 units and consumer B demands 8 units at price ₹5, market demand = 18 units at ₹5.

## Movement Along Demand Curve

A movement along the demand curve (also called expansion or contraction of demand) occurs when the **price of the good changes**, while all other factors remain constant.

- **Downward movement (expansion):** Price falls → quantity demanded rises
- **Upward movement (contraction):** Price rises → quantity demanded falls

## Shift in Demand Curve

A shift in the demand curve occurs when demand changes due to factors **other than the own price** of the good.

**Rightward shift (increase in demand):**
- Rise in consumer income (for normal goods)
- Rise in price of substitute goods
- Fall in price of complementary goods
- Favourable change in tastes

**Leftward shift (decrease in demand):**
- Fall in income (for normal goods)
- Fall in price of substitutes
- Rise in price of complements
- Unfavourable change in tastes

## Price Elasticity of Demand

Price Elasticity of Demand (PED) measures the responsiveness of quantity demanded to a change in price.

**Formula:** Ed = (% Change in Quantity Demanded) / (% Change in Price)

**Or:** Ed = (ΔQ/Q) / (ΔP/P) = (ΔQ/ΔP) × (P/Q)

Since demand is usually inversely related to price, Ed is negative. We often use the absolute value |Ed|.

**Types of Price Elasticity:**
- **Perfectly Elastic:** Ed = ∞ (horizontal demand curve)
- **Relatively Elastic:** |Ed| > 1 (luxury goods)
- **Unitary Elastic:** |Ed| = 1
- **Relatively Inelastic:** |Ed| < 1 (necessities)
- **Perfectly Inelastic:** Ed = 0 (vertical demand curve)

## Measurement of Elasticity

### Percentage Method (Proportionate Method)

**Ed = (ΔQ/Q × 100) / (ΔP/P × 100)**

**Example:** Price falls from ₹10 to ₹8 (ΔP = -2), Quantity rises from 100 to 130 (ΔQ = +30)

Ed = (30/100) / (-2/10) = 0.3 / (-0.2) = -1.5 → |Ed| = 1.5 (elastic)

### Geometric Method (Point Elasticity)

Used to measure elasticity at a specific point on the demand curve.

**Ed = Lower segment / Upper segment** (of the demand curve at that point)

For a straight-line demand curve from A (on Y-axis) to B (on X-axis):
- At midpoint: Ed = 1
- Above midpoint: Ed > 1 (elastic)
- Below midpoint: Ed < 1 (inelastic)
- At Y-axis (A): Ed = ∞
- At X-axis (B): Ed = 0

## Factors Affecting Elasticity of Demand

1. **Nature of good:** Necessities are inelastic; luxuries are elastic
2. **Availability of substitutes:** More substitutes → more elastic
3. **Proportion of income spent:** Larger proportion → more elastic
4. **Time period:** Longer time period → more elastic (more adjustments possible)
5. **Number of uses:** More uses → more elastic (e.g., electricity)
6. **Habits and addiction:** Addictive goods are inelastic (e.g., tobacco)

## Elasticity and Expenditure

The relationship between price elasticity and total expenditure (revenue):

| Elasticity | Price Falls | Price Rises |
|---|---|---|
| Elastic (|Ed| > 1) | Expenditure rises | Expenditure falls |
| Unitary Elastic (|Ed| = 1) | Expenditure unchanged | Expenditure unchanged |
| Inelastic (|Ed| < 1) | Expenditure falls | Expenditure rises |

**Logic:** If demand is elastic, a price fall causes a proportionately larger increase in quantity demanded → total expenditure (P × Q) rises.

---

*AHSEC HS 1st Year Economics – Demand chapter notes.*
""",
    ),

    # UNIT III – Producer Behaviour
    (
        "Production",
        [
            "Meaning of Production",
            "Production Function",
            "Short Run",
            "Long Run",
            "Total Product",
            "Average Product",
            "Marginal Product",
            "Returns to Scale",
            "Law of Diminishing Marginal Product",
            "Law of Variable Proportions",
        ],
        """# Production

## Meaning of Production

Production is the process of transforming inputs (factors of production) into outputs (goods and services). It creates utility and adds value to resources.

**Factors of Production:**
1. **Land:** Natural resources
2. **Labour:** Human effort (physical and mental)
3. **Capital:** Man-made means of production (machines, tools, buildings)
4. **Entrepreneurship:** Organising and risk-taking

## Production Function

A production function shows the technical relationship between inputs used and the output produced, given the state of technology.

**Q = f(L, K)** where Q = output, L = labour, K = capital

The production function specifies the maximum output obtainable from a given combination of inputs.

## Short Run

The short run is a period of time in which at least one factor of production is fixed. Firms can only change variable inputs (usually labour) to change output.

- **Fixed inputs:** Capital (machines, factory size)
- **Variable inputs:** Labour, raw materials

## Long Run

The long run is a period of time in which all factors of production can be varied. There are no fixed factors.

In the long run, firms can change both labour and capital, change the scale of production, and enter or exit the industry.

## Total Product (TP)

Total Product is the total quantity of output produced by a firm using given quantities of inputs.

**TP = Sum of Marginal Products**

TP initially increases at an increasing rate, then at a diminishing rate, reaches maximum, and may fall.

## Average Product (AP)

Average Product is the output produced per unit of variable input (labour).

**AP = TP / L**

## Marginal Product (MP)

Marginal Product is the additional output produced by employing one more unit of the variable input.

**MP = ΔTP / ΔL**

**Relationship between TP, AP, and MP:**
- When MP > AP → AP is rising
- When MP = AP → AP is at its maximum
- When MP < AP → AP is falling
- When MP = 0 → TP is maximum
- When MP is negative → TP is falling

## Law of Variable Proportions (Law of Diminishing Marginal Product)

The Law of Variable Proportions states that as we increase the quantity of one variable input (keeping other inputs fixed), the total product first increases at an increasing rate, then at a diminishing rate, and finally may decrease.

**Three stages:**
1. **Stage I (Increasing Returns):** MP rises; TP increases at increasing rate
2. **Stage II (Diminishing Returns):** MP falls but positive; TP increases at diminishing rate → **Rational production zone**
3. **Stage III (Negative Returns):** MP is negative; TP falls

**Assumptions:**
- Technology is constant
- Only one input is variable
- All units of variable input are homogeneous

## Returns to Scale

Returns to Scale examine what happens to output when ALL inputs are increased proportionately (long run concept).

**Types:**
- **Increasing Returns to Scale (IRS):** Output increases more than proportionately → economies of scale (specialisation, indivisibility of factors)
- **Constant Returns to Scale (CRS):** Output increases in the same proportion as inputs
- **Decreasing Returns to Scale (DRS):** Output increases less than proportionately → managerial inefficiency, resource scarcity

---

*AHSEC HS 1st Year Economics – Production chapter based on NCERT Class 11.*
""",
    ),

    (
        "Cost",
        [
            "Short Run Cost",
            "Long Run Cost",
            "Total Cost",
            "Total Fixed Cost",
            "Total Variable Cost",
            "Average Cost",
            "Average Fixed Cost",
            "Average Variable Cost",
            "Marginal Cost",
            "Cost Relationships",
        ],
        """# Cost

## Short Run Cost

In the short run, a firm has both fixed and variable inputs, leading to fixed and variable costs.

**Total Cost (TC) = Total Fixed Cost (TFC) + Total Variable Cost (TVC)**

## Total Fixed Cost (TFC)

Fixed costs are costs that do not change with the level of output. They must be paid even when output is zero.

**Examples:** Rent, interest on loans, insurance, depreciation, salaries of permanent staff.

TFC is a horizontal straight line parallel to the X-axis (output axis).

## Total Variable Cost (TVC)

Variable costs change with the level of output. At zero output, TVC = 0.

**Examples:** Raw materials, electricity, wages of temporary workers.

TVC initially increases at a decreasing rate (increasing returns), then at an increasing rate (diminishing returns). TVC has an S-shape.

## Total Cost (TC)

TC = TFC + TVC

TC curve has the same shape as TVC (S-shaped) but shifted upward by the amount of TFC.

## Average Cost (AC)

Average Cost (also called Average Total Cost, ATC) is the cost per unit of output.

**AC = TC / Q = AFC + AVC**

The AC curve is U-shaped:
- Falls initially (due to spreading of fixed costs + increasing returns)
- Reaches minimum at the optimum output
- Rises thereafter (due to diminishing returns)

## Average Fixed Cost (AFC)

**AFC = TFC / Q**

AFC continuously declines as output increases (fixed costs spread over more units). The AFC curve is a rectangular hyperbola — it never touches the axes but approaches them.

## Average Variable Cost (AVC)

**AVC = TVC / Q**

The AVC curve is also U-shaped:
- Falls initially (increasing returns to variable input)
- Reaches minimum
- Rises (diminishing returns)

AVC reaches minimum before AC does.

## Marginal Cost (MC)

Marginal Cost is the additional cost incurred by producing one more unit of output.

**MC = ΔTC / ΔQ = ΔTVC / ΔQ**

(Note: Fixed costs do not change with output, so MC depends only on variable costs.)

**MC Curve is U-shaped:**
- Falls initially (increasing returns)
- Reaches minimum
- Rises (diminishing returns)

**MC cuts both AVC and AC at their minimum points** (from below).

## Long Run Cost

In the long run, all costs are variable. The Long Run Average Cost (LRAC) curve is derived from the envelope of all short-run AC curves.

The LRAC is also U-shaped (but flatter):
- **Falling LRAC:** Economies of scale
- **Minimum LRAC:** Optimum scale
- **Rising LRAC:** Diseconomies of scale

## Cost Relationships

| Relationship | Explanation |
|---|---|
| TC = TFC + TVC | Basic cost identity |
| AC = AFC + AVC | Per-unit breakdown |
| MC = ΔTVC/ΔQ | Only variable costs change |
| MC cuts AC at AC minimum | Mathematical relationship |
| AFC always falls | Fixed cost spread over more units |

---

*AHSEC HS 1st Year Economics – Cost Theory notes.*
""",
    ),

    (
        "Revenue",
        [
            "Total Revenue",
            "Average Revenue",
            "Marginal Revenue",
            "Revenue Relationships",
        ],
        """# Revenue

## Total Revenue (TR)

Total Revenue is the total income earned by a firm from selling a given quantity of output.

**TR = Price (P) × Quantity (Q)**

**Under Perfect Competition:** Price is constant (price taker), so TR increases linearly.
**Under Monopoly/Imperfect Competition:** To sell more, price must be reduced, so TR first rises, reaches maximum, then falls.

## Average Revenue (AR)

Average Revenue is the revenue earned per unit of output sold.

**AR = TR / Q = Price**

AR is always equal to price. The **AR curve is the demand curve** of the firm.

- Under perfect competition: AR = constant (horizontal line)
- Under monopoly: AR is downward sloping (demand curve)

## Marginal Revenue (MR)

Marginal Revenue is the additional revenue earned from selling one more unit of output.

**MR = ΔTR / ΔQ = TRn – TR(n-1)**

**Relationship between AR and MR:**
- Under perfect competition: AR = MR (horizontal, constant price)
- Under monopoly: MR < AR and MR falls twice as fast as AR

**When TR is maximum:** MR = 0
**When TR is falling:** MR is negative

## Revenue Relationships

| Market Structure | AR | MR | Relation |
|---|---|---|---|
| Perfect Competition | Constant | = AR | AR = MR |
| Monopoly | Falling | Below AR, Falling | MR < AR |
| Monopolistic | Falling | Below AR | MR < AR |

**TR, AR, MR under perfect competition:**
- TR rises linearly
- AR = MR = constant Price

**TR, AR, MR under monopoly:**
- TR rises, reaches max when MR = 0, then falls
- AR is downward demand curve
- MR falls faster than AR (MR = AR - (ΔP/ΔQ × Q))

---

*AHSEC HS 1st Year Economics – Revenue chapter.*
""",
    ),

    (
        "Profit Maximisation",
        [
            "Conditions of Profit Maximisation",
        ],
        """# Profit Maximisation

## Conditions of Profit Maximisation

A firm aims to maximise profit, which is the difference between Total Revenue (TR) and Total Cost (TC).

**Profit (π) = TR – TC**

A firm maximises profit at the output level where profit is greatest.

### Two Conditions for Profit Maximisation:

**Condition 1 (Necessary Condition):**
**MR = MC**

At the profit-maximising output, Marginal Revenue must equal Marginal Cost. If:
- MR > MC → Producing more adds more to revenue than cost → increase output
- MR < MC → Producing more adds more to cost than revenue → decrease output
- MR = MC → Profit is maximised (no incentive to change output)

**Condition 2 (Sufficient Condition):**
**MC must be rising (MC curve cuts MR from below)**

This ensures we are at a profit maximum, not minimum. If MC is falling at the MR = MC point, it is a profit minimum.

### Graphical Approach:
- Plot TR and TC curves
- Maximum vertical gap between TR and TC (where TR > TC) = maximum profit point
- At this output, slope of TR = slope of TC, i.e., MR = MC

### Using TR–TC Approach:
- Where TR – TC is maximum → profit is maximised
- The slope of TR (= MR) equals the slope of TC (= MC) at this point

### Shut-Down Condition:
A firm continues to produce in the short run as long as **P ≥ AVC** (Price covers variable cost).
- If P < AVC → firm shuts down immediately (cannot even cover variable costs)
- If AVC ≤ P < AC → firm produces but makes a loss (covering some fixed cost)
- If P ≥ AC → firm makes normal or supernormal profit

---

*AHSEC HS 1st Year Economics – Profit Maximisation notes.*
""",
    ),

    (
        "Supply",
        [
            "Meaning of Supply",
            "Determinants of Supply",
            "Law of Supply",
            "Supply Schedule",
            "Short Run Supply Curve",
            "Long Run Supply Curve",
            "Market Supply",
            "Price Elasticity of Supply",
            "Measurement of Elasticity of Supply",
        ],
        """# Supply

## Meaning of Supply

Supply refers to the quantity of a good that a producer is willing and able to offer for sale at a given price, during a given time period.

Supply represents the producer's side of the market (as demand represents the consumer's side).

## Determinants of Supply

1. **Price of the good:** Higher price → higher supply (main determinant)
2. **Input prices:** Rise in input prices → supply decreases (higher cost)
3. **Technology:** Better technology → more efficient → supply increases
4. **Number of firms:** More firms → market supply increases
5. **Government policy:** Taxes reduce supply; subsidies increase supply
6. **Prices of related goods:** If price of substitute good rises → supply of original falls (producer switches)
7. **Expectations:** If future prices expected to rise → present supply falls (hoarding)

## Law of Supply

The Law of Supply states that, other things being equal (ceteris paribus), as the price of a good rises, the quantity supplied also rises, and as price falls, quantity supplied falls.

**Direct (positive) relationship between price and quantity supplied.**

**Reason:** Higher prices mean higher profits → firms are motivated to produce more.

## Supply Schedule

A supply schedule is a tabular statement showing the quantities of a good supplied at different prices (in a given time period).

| Price (₹) | Quantity Supplied (units) |
|---|---|
| 2 | 100 |
| 4 | 150 |
| 6 | 200 |
| 8 | 250 |
| 10 | 300 |

## Short Run Supply Curve

The supply curve shows the graphical relationship between price and quantity supplied. It slopes upward from left to right (positive slope), reflecting the law of supply.

In the short run, some inputs are fixed, so the firm's supply curve is its MC curve above the AVC curve (shut-down point).

## Long Run Supply Curve

In the long run:
- All inputs are variable
- Firms can enter and exit the industry
- The long-run supply curve can be:
  - **Upward sloping** (increasing cost industry)
  - **Horizontal** (constant cost industry)
  - **Downward sloping** (decreasing cost industry — rare)

## Market Supply

Market supply is the sum of individual firm supplies at each price level.

**Market Supply = Sum of all individual firm supplies**

The market supply curve is obtained by horizontal addition of individual supply curves.

## Price Elasticity of Supply

Price Elasticity of Supply (PES) measures the responsiveness of quantity supplied to changes in price.

**Es = (% Change in Quantity Supplied) / (% Change in Price)**

**Es = (ΔQs/Qs) / (ΔP/P)**

Since supply and price move in the same direction, Es is always positive.

**Types:**
- **Perfectly Inelastic (Es = 0):** Quantity supplied does not change with price (vertical supply curve)
- **Perfectly Elastic (Es = ∞):** Suppliers supply any amount at a given price (horizontal)
- **Relatively Inelastic (Es < 1):** Quantity change < price change (necessities, perishables)
- **Unitary Elastic (Es = 1):** Quantity change = price change (straight line through origin)
- **Relatively Elastic (Es > 1):** Quantity change > price change (manufactured goods)

## Measurement of Elasticity of Supply

**Percentage Method:**
Es = (ΔQs / Qs × 100) / (ΔP / P × 100)

**Example:** Price rises from ₹10 to ₹12 (ΔP = 2), Quantity supplied rises from 200 to 260 (ΔQs = 60).

Es = (60/200) / (2/10) = 0.3 / 0.2 = 1.5 (elastic supply)

**Factors affecting Elasticity of Supply:**
1. **Nature of the good:** Perishables → inelastic; manufactured goods → elastic
2. **Time period:** Longer time → more elastic (more adjustment possible)
3. **Factor mobility:** More mobile factors → more elastic supply
4. **Ease of production:** Easy to ramp up production → elastic

---

*AHSEC HS 1st Year Economics – Supply chapter notes.*
""",
    ),

    # UNIT IV – Market Equilibrium
    (
        "Market Equilibrium",
        [
            "Equilibrium",
            "Excess Demand",
            "Excess Supply",
            "Market Equilibrium with Fixed Number of Firms",
            "Market Equilibrium with Free Entry and Exit",
        ],
        """# Market Equilibrium

## Equilibrium

Market equilibrium is the situation where the quantity demanded equals the quantity supplied at a particular price. There is no tendency to change.

**Equilibrium Condition: Qd = Qs**

**Equilibrium Price (Market-Clearing Price):** The price at which the market is cleared (no shortage, no surplus).

At equilibrium, the demand curve and supply curve intersect.

## Excess Demand

Excess Demand occurs when quantity demanded exceeds quantity supplied at a given price.

**Excess Demand = Qd – Qs > 0**

This usually happens when the price is below the equilibrium price (price is too low). Buyers want more than sellers are willing to supply.

**Effect:** Competition among buyers pushes the price up. As price rises:
- Quantity demanded falls (movement along demand curve)
- Quantity supplied rises (movement along supply curve)
- Market moves back to equilibrium

**Excess demand → price rises → equilibrium restored**

## Excess Supply

Excess Supply occurs when quantity supplied exceeds quantity demanded at a given price.

**Excess Supply = Qs – Qd > 0**

This happens when price is above equilibrium (price is too high). Sellers have unsold goods.

**Effect:** Competition among sellers pushes the price down. As price falls:
- Quantity demanded rises
- Quantity supplied falls
- Market moves back to equilibrium

**Excess supply → price falls → equilibrium restored**

## Market Equilibrium with Fixed Number of Firms

When the number of firms in the market is fixed (short run):

- Demand shift: If demand increases (rightward shift of demand curve), new equilibrium has higher price AND higher quantity.
- Supply shift: If supply increases (rightward shift), new equilibrium has lower price and higher quantity.

Market always tends toward equilibrium through price adjustments.

## Market Equilibrium with Free Entry and Exit

When firms can freely enter and exit the market (long run):

**With Supernormal Profit:**
- Existing firms earn profit → new firms enter → supply increases → price falls → profit returns to normal (zero economic profit)

**With Losses:**
- Existing firms make losses → some exit → supply decreases → price rises → losses eliminated

**Long-run equilibrium:** Price = AC (zero economic profit) and P = MC (productive efficiency).

In the long run, price in a competitive market settles at the minimum AC of production.

---

*AHSEC HS 1st Year Economics – Market Equilibrium notes.*
""",
    ),

    (
        "Applications of Demand and Supply",
        [
            "Price Ceiling",
            "Price Floor",
        ],
        """# Applications of Demand and Supply

## Price Ceiling

A price ceiling is a government-imposed maximum price (legal price) that is set **below** the equilibrium price. The price is not allowed to rise above this ceiling.

**Purpose:** To make essential goods affordable to consumers (e.g., food, medicines, rent).

**Effect:**
- At the ceiling price (Pc < Pe): Quantity demanded > Quantity supplied
- **Shortage (Excess Demand)** is created
- Black markets may emerge (goods sold illegally at higher prices)
- Rationing may be introduced (e.g., ration cards)

**Examples in India:**
- Essential Commodities Act
- Maximum retail prices (MRP) on medicine
- Fair Price Shops (PDS) – government subsidised food prices

**Problems of Price Ceiling:**
1. Creates shortages
2. Encourages black marketing
3. May reduce quality of goods
4. Requires rationing mechanisms

## Price Floor

A price floor is a government-imposed minimum price that is set **above** the equilibrium price. Price cannot fall below this floor.

**Purpose:** To protect producers from low prices (e.g., farmers, labour minimum wage).

**Effect:**
- At the floor price (Pf > Pe): Quantity supplied > Quantity demanded
- **Surplus (Excess Supply)** is created
- Government may need to purchase the surplus

**Examples in India:**
- **Minimum Support Price (MSP) for farmers:** Government guarantees a minimum price for agricultural produce (wheat, rice, etc.)
- **Minimum Wage Law:** Ensures workers are paid at least a minimum wage (floor on wage price)

**Problems of Price Floor:**
1. Creates surplus / excess supply
2. Government has to buy up surplus (costly)
3. May discourage efficiency
4. Can create unemployment (if wage floor > equilibrium wage)

**Summary:**

| Feature | Price Ceiling | Price Floor |
|---|---|---|
| Set | Below equilibrium | Above equilibrium |
| Result | Shortage | Surplus |
| Purpose | Protect consumers | Protect producers |
| Examples | MRP on medicines | MSP for farmers, Minimum Wage |

---

*AHSEC HS 1st Year Economics – Applications of Demand and Supply (NCERT Class 11).*
""",
    ),

    # ── PART B: STATISTICS FOR ECONOMICS ─────────────────────────────────────

    # UNIT I
    (
        "Statistics in Economics",
        [
            "Meaning of Statistics",
            "Scope of Statistics",
            "Importance of Statistics in Economics",
        ],
        """# Statistics in Economics

## Meaning of Statistics

Statistics can be understood in two senses:

**Singular Sense (Science):** Statistics refers to the science of collecting, organising, presenting, analysing, and interpreting numerical data.

**Plural Sense (Data):** Statistics refers to numerical data itself — e.g., "statistics of national income."

**Definition:** Statistics is the science that deals with the collection, organisation, presentation, analysis, and interpretation of quantitative data to aid in decision-making.

**Characteristics of Statistics:**
- Data are aggregate of facts (not single observations)
- Data are expressed numerically
- Data are affected by multiple causes
- Data are collected with a specific purpose
- Data are comparable

## Scope of Statistics

Statistics has a wide scope across various fields:

**In Economics:**
- National income estimation (GDP, NNP)
- Index numbers (price indices, industrial production index)
- Demand forecasting
- Study of inflation, unemployment, poverty

**In Business:**
- Market research
- Quality control
- Financial analysis
- Operations management

**In Government:**
- Census operations
- Budget planning
- Social welfare programmes

**In Research:**
- Scientific experiments
- Social surveys
- Policy evaluation

**Limitations of Statistics:**
1. Only studies numerical phenomena
2. Does not study individual observations
3. Results are only approximately true
4. Can be misused ("Lies, damned lies, and statistics")
5. Requires expertise for proper interpretation

## Importance of Statistics in Economics

1. **Study of Economic Problems:** Statistics help quantify and analyse economic issues like poverty, unemployment, and inflation.

2. **Economic Planning:** Government uses statistics for planning (e.g., Five-Year Plans in India used statistical data on production, income, population).

3. **Economic Laws:** Laws like Demand Law are verified using statistical data.

4. **Business Decisions:** Firms use market research statistics for pricing, production, and investment decisions.

5. **International Comparisons:** Per capita income, HDI, trade statistics allow comparison between countries.

6. **Testing Economic Theories:** Econometrics uses statistical tools to test and validate economic theories.

7. **Policy Formulation:** Government policies on taxation, subsidies, and welfare programmes are based on statistical analysis.

---

*AHSEC HS 1st Year Economics – Statistics in Economics chapter.*
""",
    ),

    # UNIT II
    (
        "Collection of Data",
        [
            "Primary Data",
            "Secondary Data",
            "Methods of Collection",
            "Census Method",
            "Sample Survey",
            "Census of India",
            "National Sample Survey Organisation",
        ],
        """# Collection of Data

## Primary Data

Primary data are data collected for the first time, directly from the source (original data). They are specifically collected for a particular investigation.

**Methods of collecting primary data:**
1. Direct Personal Interview
2. Indirect Oral Interview
3. Questionnaire method (mailed)
4. Schedules filled by enumerators
5. Observation method

**Merits of Primary Data:**
- More reliable and accurate
- Specific to the purpose
- Current and up-to-date
- Collected under controlled conditions

**Demerits:**
- Time-consuming and expensive
- Requires trained personnel
- Scope is limited to one study

## Secondary Data

Secondary data are data that have already been collected by someone else for some other purpose and are now being used by the researcher.

**Sources of secondary data:**
- Government publications (RBI, CSO, NSSO reports)
- International organisations (UN, World Bank, IMF)
- Journals and academic papers
- Newspapers and magazines
- Websites and databases

**Merits:**
- Less expensive and time-saving
- Wide coverage
- Historical data available

**Demerits:**
- May not fit the current purpose
- Reliability must be checked
- May be outdated
- Difficult to know collection methods

## Methods of Collection

**For Primary Data:**
1. **Direct Personal Investigation:** Investigator personally gathers information; accurate but costly and limited in scope.
2. **Indirect Oral Investigation:** Information obtained through third parties (witnesses, experts); suitable when direct data is difficult.
3. **Information from Correspondents:** Local agents or correspondents send data; suitable for newspapers and government data.
4. **Mailed Questionnaire:** Questionnaire sent by post; covers large area but low response rate.
5. **Schedules through Enumerators:** Trained enumerators fill schedules; more reliable, used in Census.

## Census Method

The Census Method (Complete Enumeration) involves collecting data from every single unit of the population (universe).

**Example:** Population Census of India — every household is surveyed.

**Merits:**
- Comprehensive and accurate
- No sampling error
- Useful when population is small or data must be exhaustive

**Demerits:**
- Very expensive and time-consuming
- Not feasible for very large populations
- Requires large number of trained investigators

## Sample Survey

A Sample Survey involves collecting data from only a portion (sample) of the population and using it to make inferences about the whole population.

**Merits:**
- Less expensive and faster
- Feasible for large populations
- Can be used for destructive testing
- More detailed information per unit

**Demerits:**
- Sampling error is present
- Requires careful selection of sample
- Results may not perfectly represent the whole population

**Methods of Sampling:**
- **Random Sampling:** Every unit has an equal chance of selection
- **Stratified Sampling:** Population divided into strata; random selection from each stratum
- **Systematic Sampling:** Every nth unit is selected
- **Cluster Sampling:** Population divided into clusters; some clusters fully surveyed

## Census of India

The Census of India is the largest data collection exercise in the world. It is conducted every 10 years.

- First comprehensive Census: 1881
- Latest Census: 2011 (2021 Census delayed due to COVID-19)
- Provides data on population, age, sex, literacy, occupation, housing, etc.
- Conducted by the Office of the Registrar General of India (RGI)

**Key data from Census 2011:**
- Population: 1.21 billion
- Literacy rate: 74%
- Sex ratio: 940 females per 1000 males

## National Sample Survey Organisation (NSSO)

The National Sample Survey Office (NSSO) is a government organisation that conducts nationwide surveys on various socio-economic topics.

- Established: 1950
- Conducts annual surveys on employment, income, consumer expenditure, health, education
- Major surveys: NSS Employment-Unemployment Survey, Household Consumer Expenditure Survey
- Data is used for government planning and policy-making

**Important NSSO surveys:**
- Periodic Labour Force Survey (PLFS) — employment and wages
- Household Consumer Expenditure Survey (HCE) — poverty estimation

---

*AHSEC HS 1st Year Economics – Collection of Data chapter.*
""",
    ),

    (
        "Organisation of Data",
        [
            "Meaning of Organisation",
            "Variables",
            "Types of Variables",
            "Frequency",
            "Frequency Distribution",
        ],
        """# Organisation of Data

## Meaning of Organisation of Data

Organisation of data refers to the systematic arrangement of collected data in a form that makes it easy to analyse and interpret. Raw data collected from surveys must be organised before analysis.

**Steps in data organisation:**
1. Editing (checking for errors)
2. Classification (grouping similar data)
3. Tabulation (presenting in tables)

## Variables

A variable is a characteristic that can take different values. In statistics, the quantity being measured is a variable.

**Examples:** Height, income, age, marks obtained.

## Types of Variables

**1. Continuous Variable:**
A continuous variable can take any value within a given range (including fractions and decimals).

**Examples:** Height (165.5 cm), temperature (37.2°C), weight (62.3 kg).

**2. Discrete Variable:**
A discrete variable can only take specific, countable values (usually integers). Fractional values are not possible.

**Examples:** Number of students (30, 31, 32...), number of children in a family.

## Frequency

Frequency is the number of times a particular value (or class) appears in a dataset.

**Example:** In the data {5, 3, 5, 7, 5, 3}, the frequency of 5 is 3, frequency of 3 is 2, and frequency of 7 is 1.

## Frequency Distribution

A frequency distribution is a table that shows the values (or class intervals) and their corresponding frequencies.

**Simple/Ungrouped Frequency Distribution:**
Shows individual values and their frequencies. Used when there are few distinct values.

**Grouped Frequency Distribution:**
Data is grouped into class intervals. Used when there are many values.

**Terms used:**
- **Class Interval (Class Width):** The range covered by each class (e.g., 0-10, 10-20, 20-30)
- **Class Limits:** The lowest (lower limit) and highest (upper limit) values of a class
- **Class Boundaries:** Exact boundaries of a class (adjusting for gaps between classes)
- **Class Frequency:** Number of observations in a class
- **Relative Frequency:** Frequency of a class as a proportion of total frequency
- **Cumulative Frequency:** Running total of frequencies up to a class

**Example:**

| Marks (Class Interval) | Tally | Frequency |
|---|---|---|
| 0 – 20 | III | 3 |
| 20 – 40 | IIII I | 6 |
| 40 – 60 | IIII IIII | 9 |
| 60 – 80 | IIII | 4 |
| 80 – 100 | II | 2 |
| **Total** | | **24** |

**Types of class intervals:**
- **Exclusive (open-end):** Upper limit of one class = lower limit of next (10-20, 20-30)
- **Inclusive (closed-end):** Both limits included (10-19, 20-29)

---

*AHSEC HS 1st Year Economics – Organisation of Data chapter.*
""",
    ),

    (
        "Presentation of Data",
        [
            "Tabular Presentation",
            "Diagrammatic Presentation",
            "Bar Diagram",
            "Pie Diagram",
            "Histogram",
            "Frequency Polygon",
            "Frequency Curve",
            "Ogive",
            "Time Series Graph",
        ],
        """# Presentation of Data

## Tabular Presentation

Tabular presentation organises data into rows and columns in a table. It makes data compact and easy to compare.

**Parts of a table:**
1. Table number
2. Title
3. Caption (column headings)
4. Stub (row headings)
5. Body of the table
6. Head note (if any)
7. Footnote and source

**Types of tables:**
- Simple table (one characteristic)
- Complex table (two or more characteristics)

## Diagrammatic Presentation

Diagrammatic presentation uses visual tools (diagrams, graphs, charts) to display data. It makes data attractive and easy to understand.

**Types of diagrams:** Bar diagrams, pie charts, pictograms, line graphs, histograms.

**Merits:** Visually appealing, easy to compare, can be understood without data literacy.
**Demerits:** Less precise, cannot show exact values.

## Bar Diagram

A bar diagram uses rectangular bars (horizontal or vertical) whose lengths are proportional to the values they represent.

**Types:**
- **Simple Bar Diagram:** One variable
- **Multiple (Grouped) Bar Diagram:** Two or more variables side by side
- **Sub-divided (Stacked) Bar Diagram:** Bars divided into components
- **Percentage Bar Diagram:** Bars show 100% with component percentages
- **Horizontal Bar Diagram:** Bars drawn horizontally (good for long category names)

## Pie Diagram (Pie Chart)

A pie chart is a circular chart divided into sectors. Each sector's area is proportional to the frequency (or percentage) it represents.

**Construction:**
Angle for each component = (Component Value / Total) × 360°

**Example:** If agriculture = 40% of GDP → angle = 0.4 × 360° = 144°

## Histogram

A histogram is a bar chart for continuous (grouped) data where:
- X-axis shows class intervals
- Y-axis shows frequency (or frequency density)
- Bars are adjacent (no gaps between bars)

**Key difference from bar chart:** In histograms, bars touch each other (no gaps); suitable for continuous data.

**Area of each bar = Frequency** (when class widths are equal)
**For unequal class widths:** Y-axis shows frequency density = Frequency / Class Width

## Frequency Polygon

A frequency polygon is a line graph drawn by connecting the midpoints of the top of each bar in a histogram.

**Construction:**
1. Find midpoints of each class interval
2. Plot midpoints vs. frequencies
3. Join points with straight lines
4. Close the polygon at both ends by extending to adjacent midpoints with zero frequency

## Frequency Curve

A frequency curve is a smooth curve obtained from a frequency polygon by smoothing out the straight lines. It represents the ideal frequency distribution of a large dataset.

**Types of frequency curves:**
- Symmetrical (Bell-shaped / Normal curve)
- Positively skewed (tail on right)
- Negatively skewed (tail on left)
- U-shaped

## Ogive (Cumulative Frequency Curve)

An Ogive is a graph of cumulative frequencies plotted against the upper or lower class boundaries.

**Types:**
- **Less than Ogive:** Plot cumulative frequency against upper class boundary (rises from left to right)
- **More than Ogive:** Plot cumulative frequency against lower class boundary (falls from left to right)

**Use:** Finding median, quartiles, and percentiles graphically. The X-value at the intersection of Less-than and More-than Ogives = Median.

## Time Series Graph

A time series graph (line graph) shows how a variable changes over time.

- X-axis: Time (years, months, quarters)
- Y-axis: Value of the variable
- Points are joined by straight lines

**Example:** GDP growth rate of India from 2010 to 2020.

**Uses:**
- Identify trends (upward, downward, cyclical)
- Seasonal variation analysis
- Economic forecasting

---

*AHSEC HS 1st Year Economics – Presentation of Data chapter.*
""",
    ),

    # UNIT III
    (
        "Measures of Central Tendency",
        [
            "Arithmetic Mean",
            "Median",
            "Mode",
        ],
        """# Measures of Central Tendency

A measure of central tendency is a single value that represents the entire dataset by indicating the central point of the distribution.

**Three main measures:**
1. Arithmetic Mean
2. Median
3. Mode

## Arithmetic Mean

The Arithmetic Mean (AM) is the sum of all observations divided by the number of observations.

**For ungrouped data:**
**Mean (X̄) = ΣX / n**

**For grouped data (direct method):**
**Mean = Σ(f × m) / Σf**
Where f = frequency, m = midpoint of class interval

**Properties of Mean:**
- Uses all data values
- Algebraic treatment is possible
- Affected by extreme values (outliers)
- Sum of deviations from mean = 0

**Merits:** Mathematically sound, based on all observations, useful for further calculations.
**Demerits:** Affected by outliers; cannot be calculated for open-end class intervals easily.

## Median

The Median is the value that divides the dataset into two equal halves when data is arranged in ascending or descending order. It is the middle value.

**For ungrouped data:**
- If n is odd: Median = value of ((n+1)/2)th item
- If n is even: Median = average of (n/2)th and (n/2 + 1)th items

**For grouped data:**
**Median = L + [(n/2 – cf) / f] × h**

Where:
- L = lower boundary of median class
- n = total frequency
- cf = cumulative frequency before median class
- f = frequency of median class
- h = class width

**Properties:**
- Not affected by extreme values
- Can be determined graphically (from Ogive)
- Cannot be used for algebraic manipulation

**Merits:** Not affected by outliers; can be found for open-end class intervals.
**Demerits:** Not based on all observations; less precise.

## Mode

The Mode is the value that occurs most frequently in a dataset.

**For ungrouped data:** The value with the highest frequency.

**For grouped data:**
**Mode = L + [f1 – f0 / (2f1 – f0 – f2)] × h**

Where:
- L = lower boundary of modal class
- f1 = frequency of modal class
- f0 = frequency of class before modal class
- f2 = frequency of class after modal class
- h = class width

**Modal class:** The class with the highest frequency.

**Properties:**
- Not affected by extreme values
- Can be found for qualitative data too
- A dataset may have no mode, one mode (unimodal), or two modes (bimodal)

**Empirical relationship:**
**Mode = 3 × Median – 2 × Mean** (for moderately skewed distributions)

**Summary Table:**

| Measure | Formula | Affected by Extremes? | Best Used When |
|---|---|---|---|
| Mean | ΣX/n | Yes | Symmetric distribution, no outliers |
| Median | Middle value | No | Skewed data, open-end classes |
| Mode | Most frequent | No | Qualitative data, most common value needed |

---

*AHSEC HS 1st Year Economics – Measures of Central Tendency (Statistics chapter).*
""",
    ),

    (
        "Correlation",
        [
            "Meaning of Correlation",
            "Properties of Correlation",
            "Scatter Diagram",
            "Karl Pearson's Method",
            "Spearman's Rank Correlation",
        ],
        """# Correlation

## Meaning of Correlation

Correlation measures the degree and direction of the linear relationship between two variables. When two variables tend to move together, they are said to be correlated.

**Examples:**
- Height and weight (positive correlation)
- Price and demand (negative correlation)
- Shoe size and intelligence (no correlation)

**Types of Correlation:**

**Based on Direction:**
- **Positive Correlation:** Both variables move in the same direction (X↑ → Y↑)
- **Negative Correlation:** Variables move in opposite directions (X↑ → Y↓)

**Based on Degree:**
- **Perfect Positive (r = +1):** Exact linear relationship, positive direction
- **Perfect Negative (r = -1):** Exact linear relationship, negative direction
- **Zero Correlation (r = 0):** No linear relationship

**Based on Nature:**
- **Linear:** Relationship is a straight line
- **Non-linear (Curvilinear):** Relationship is a curve

## Properties of Correlation

1. **Range:** Correlation coefficient (r) lies between -1 and +1: **-1 ≤ r ≤ +1**
2. **Dimensionless:** r has no units
3. **Symmetrical:** Correlation of X with Y = Correlation of Y with X
4. **Not affected by change of origin or scale** (for Pearson's r)
5. **Independent of units of measurement**

## Scatter Diagram

A scatter diagram (scatter plot) is a graphical method of studying correlation. Values of both variables are plotted as dots on a graph.

**Interpretation:**
- **Upward sloping cloud of dots → Positive correlation**
- **Downward sloping cloud → Negative correlation**
- **Random scatter → No correlation**
- **Dots on a perfect line → Perfect correlation (r = ±1)**
- **Oval/elliptical cloud → Moderate correlation**

**Merits:** Simple, visual, doesn't assume linearity.
**Demerits:** Doesn't give exact value of correlation.

## Karl Pearson's Coefficient of Correlation

Karl Pearson's method gives a precise numerical value of correlation for quantitative data.

**Formula:**
**r = Σ(dx)(dy) / √[Σdx² × Σdy²]**

Where:
- dx = X – X̄ (deviation of X from mean)
- dy = Y – Ȳ (deviation of Y from mean)

**Alternative formula (actual mean method):**
**r = [n·ΣXY – ΣX·ΣY] / √[(nΣX² – (ΣX)²)(nΣY² – (ΣY)²)]**

**Interpretation of r:**
- r = +1: Perfect positive correlation
- r = -1: Perfect negative correlation
- 0.75 ≤ |r| < 1: High correlation
- 0.5 ≤ |r| < 0.75: Moderate correlation
- 0.25 ≤ |r| < 0.5: Low correlation
- |r| < 0.25: Negligible correlation
- r = 0: No linear correlation

**Assumptions:**
1. Variables are linearly related
2. Normal distribution of variables
3. Quantitative data

## Spearman's Rank Correlation

Spearman's Rank Correlation is used when data is available in rank form, or when data is qualitative, or when the relationship is not linear.

**Formula:**
**rs = 1 – [6·ΣD² / n(n² – 1)]**

Where:
- D = Difference between ranks of corresponding values (R1 – R2)
- n = Number of paired observations

**Steps:**
1. Assign ranks to both variables (1 = highest or lowest, consistently)
2. Find D = R1 – R2 for each pair
3. Calculate D²
4. Apply formula

**When ranks are tied:** Assign the average of the tied ranks.

**Interpretation:** Same as Pearson's r (-1 to +1).

**Merits:** Can be used for ordinal data; not affected by outliers; simpler to calculate.
**Demerits:** Less precise than Pearson's for quantitative data; loses information by using only ranks.

---

*AHSEC HS 1st Year Economics – Correlation chapter.*
""",
    ),

    (
        "Index Numbers",
        [
            "Meaning of Index Numbers",
            "Construction of Index Numbers",
            "Wholesale Price Index",
            "Consumer Price Index",
            "Index of Industrial Production",
            "Uses of Index Numbers",
        ],
        """# Index Numbers

## Meaning of Index Numbers

An index number is a statistical measure designed to show changes in a variable (or group of variables) over time, with reference to a base period.

**Index Number = (Current Period Value / Base Period Value) × 100**

The base period has index = 100.

**Example:** If price of wheat was ₹20 in 2010 (base) and ₹30 in 2020, then price index = (30/20) × 100 = 150. This means prices have risen by 50%.

**Key features:**
- Expressed as a percentage
- Relative measure of change
- Base year index = 100

## Construction of Index Numbers

**Steps:**
1. **Choose base year:** A normal year (no extreme events)
2. **Select commodities:** Decide which items to include (should be representative)
3. **Collect price data:** Current period and base period prices
4. **Calculate index:** Using appropriate method

**Methods of construction:**

**1. Simple (Unweighted) Price Index:**
**P₀₁ = (ΣP₁ / ΣP₀) × 100**

All items given equal weight.

**2. Weighted Index – Laspeyres Method:**
**P₀₁ = (ΣP₁Q₀ / ΣP₀Q₀) × 100**

Base year quantities (Q₀) used as weights.

**3. Weighted Index – Paasche's Method:**
**P₀₁ = (ΣP₁Q₁ / ΣP₀Q₁) × 100**

Current year quantities (Q₁) used as weights.

**4. Fisher's Ideal Index:**
**P₀₁ = √(Laspeyres × Paasche) × 100**

Geometric mean of Laspeyres and Paasche indices. Called "ideal" because it satisfies the time reversal test and factor reversal test.

## Wholesale Price Index (WPI)

The Wholesale Price Index measures changes in the prices of goods at the wholesale level (before reaching consumers).

**In India:**
- Base year: 2011-12
- Covers about 697 commodities
- Published by Office of Economic Adviser, Ministry of Commerce
- Used to measure general price inflation in the economy

**Major groups in WPI:**
1. Primary Articles (food, non-food, minerals) — weight ~22%
2. Fuel and Power — weight ~13%
3. Manufactured Products — weight ~65%

## Consumer Price Index (CPI)

The Consumer Price Index measures changes in the prices of a basket of goods and services typically purchased by consumers.

**In India (CPI-Combined):**
- Base year: 2012 = 100
- Compiled by Ministry of Statistics (MoSPI)
- Published monthly
- Used by RBI for monetary policy decisions

**Sub-groups in CPI:**
- Food and Beverages (~45% weight)
- Housing
- Clothing and Footwear
- Fuel and Light
- Miscellaneous

**CPI vs WPI:**
| Feature | CPI | WPI |
|---|---|---|
| Coverage | Consumer prices | Wholesale prices |
| Level | Retail | Wholesale |
| Services | Included | Not included |
| Used for | Real wages, monetary policy | General inflation |

## Index of Industrial Production (IIP)

The Index of Industrial Production measures the volume of industrial output (not prices).

**In India:**
- Base year: 2011-12
- Covers Mining, Manufacturing, Electricity sectors
- Published monthly by MoSPI
- Shows growth in industrial production

**Three components:**
1. Mining (weight ~14%)
2. Manufacturing (weight ~77%)
3. Electricity (weight ~8%)

## Uses of Index Numbers

1. **Measuring Inflation:** CPI and WPI track price changes → helps control inflation
2. **Cost of Living:** CPI helps workers demand wage revisions based on price changes
3. **Real Wage Calculation:** Real Wage = Nominal Wage / CPI × 100
4. **Economic Comparisons:** Compare economic conditions across time periods and countries
5. **Business Decisions:** Firms use commodity price indices for pricing decisions
6. **Government Policy:** Budget, monetary policy, and subsidy decisions use index data
7. **Deflating Values:** Convert nominal GDP to real GDP using price deflators
8. **Wage Negotiations:** Dearness Allowance (DA) linked to CPI in India

**Deflating National Income:**
**Real NI = (Nominal NI / Price Index) × 100**

---

*AHSEC HS 1st Year Economics – Index Numbers chapter.*
""",
    ),
]


# ─── Political Science HS 1st Year – AHSEC Arts ──────────────────────────────

POLITICAL_SCIENCE_CHAPTERS = [
    (
        "Political Theory: An Introduction",
        [
            "What is Politics",
            "What is Political Theory",
            "Why Study Political Theory",
            "Approaches to Political Theory",
            "Normative and Empirical Approaches",
        ],
        """# Political Theory: An Introduction

## What is Politics

Politics refers to activities associated with making collective decisions for a society, governing a state, and managing public affairs. It involves:

- Exercise of power and authority
- Making decisions binding on all members of society
- Resolution of conflicts through legitimate means
- Distribution of resources in society

**Broadly:** Politics is the art of governance and the study of how societies organise themselves and make collective decisions.

## What is Political Theory

Political theory is the systematic study of the concepts, principles, and ideals that shape political life and governance. It asks:

- What is justice?
- What is liberty?
- What is the best form of government?
- What are the rights of citizens?

**Political theory differs from:**
- Political science (empirical study of political institutions)
- Political philosophy (speculative, normative questions)

## Why Study Political Theory

1. **Understanding politics:** Provides tools to analyse political events and institutions
2. **Developing citizenship:** Helps citizens understand their rights and duties
3. **Evaluating policies:** Framework for judging government policies
4. **Promoting justice:** Ideas of equality and justice guide political reform
5. **Critical thinking:** Encourages questioning established norms and institutions

## Approaches to Political Theory

**Traditional Approach:**
- Focuses on philosophical analysis of political ideas
- Study of great thinkers (Plato, Aristotle, Locke, Rousseau)
- Normative in character (what ought to be)

**Modern (Behavioural) Approach:**
- Empirical, scientific analysis
- Focus on observable political behaviour
- Value-free, objective

## Normative and Empirical Approaches

**Normative Approach:**
- Deals with values, ideals, and what ought to be
- Examples: justice, equality, freedom
- Cannot be empirically tested

**Empirical Approach:**
- Deals with facts and what is
- Can be tested and verified
- Examples: voting behaviour, election results, party systems

---

*AHSEC HS 1st Year Political Science – Political Theory: An Introduction.*
""",
    ),

    (
        "Freedom",
        [
            "What is Freedom",
            "Negative Liberty",
            "Positive Liberty",
            "Freedom of Expression",
            "Constraints on Freedom",
        ],
        """# Freedom

## What is Freedom

Freedom means the absence of constraints on individual action and the ability to live in a self-directed manner. It is a fundamental value in political theory.

**Two key aspects:**
1. **Negative freedom:** Absence of external interference/constraints
2. **Positive freedom:** Ability and capacity to act (self-realisation)

## Negative Liberty

Negative liberty focuses on freedom **from** interference. It means the absence of external obstacles or coercion that prevent individuals from doing what they want.

**Key thinker:** Isaiah Berlin ("Two Concepts of Liberty," 1958)

**Features:**
- Individual has a protected private sphere free from state intervention
- State should not interfere in personal choices
- Minimum government = maximum individual freedom

**Example:** Freedom from censorship, freedom from arbitrary arrest.

## Positive Liberty

Positive liberty focuses on freedom **to** — the actual ability to fulfil one's potential. It requires enabling conditions.

**Features:**
- True freedom requires resources and capabilities
- The state may need to intervene to give people real choices
- Poverty, illiteracy, and discrimination restrict positive freedom

**Example:** Right to education enables positive freedom; welfare state enhances positive liberty.

## Freedom of Expression

Freedom of expression is the right to express one's opinions, ideas, and information without government interference.

**Includes:**
- Freedom of speech
- Freedom of press
- Freedom of assembly and association
- Artistic and creative expression

**Why important:**
- Essential for democracy (informed citizenry)
- Enables political participation
- Protects minority opinions
- Basis for truth-finding (marketplace of ideas)

**Reasonable Restrictions:** Freedom of expression is not absolute. It can be restricted for:
- Public order and security
- National security
- Protection of reputation (defamation laws)
- Decency and morality

## Constraints on Freedom

Some constraints on freedom are necessary for ordered social life. Freedom can be limited to:

1. **Protect others' freedom:** Your freedom ends where another's begins
2. **Prevent harm:** John Stuart Mill's Harm Principle (only restrict if causing harm to others)
3. **Maintain social order:** Some regulation needed for collective life
4. **Protect rights:** Right to privacy may limit press freedom

**John Stuart Mill's On Liberty (1859):** The state can only legitimately restrict freedom to prevent harm to others. All other restrictions are illegitimate.

---

*AHSEC HS 1st Year Political Science – Freedom chapter.*
""",
    ),

    (
        "Equality",
        [
            "What is Equality",
            "Natural and Social Equality",
            "Civil Equality",
            "Political Equality",
            "Economic Equality",
            "Equality and Discrimination",
        ],
        """# Equality

## What is Equality

Equality means treating all people as equals, without discrimination based on birth, race, sex, religion, or caste. It is a fundamental political value and a cornerstone of democracy.

**Two dimensions:**
1. **Formal equality:** Equal treatment under the law (equality before law)
2. **Substantive equality:** Equal opportunity and outcomes in practice

## Natural and Social Equality

**Natural equality:** The idea that all human beings are equal in certain fundamental ways (equal moral worth, equal right to dignity). Natural inequalities (physical differences) exist but should not determine social worth.

**Social equality:** Equality in social standing and relationships. Aims to eliminate social hierarchies based on caste, gender, race, or class.

## Civil Equality

Civil equality means equal civil rights for all citizens — equal access to the legal system, equal protection under law, and freedom from arbitrary discrimination.

**Examples:**
- Equality before law (Article 14, Indian Constitution)
- Right to property
- Right to freedom of religion

## Political Equality

Political equality means equal political rights for all citizens — one person, one vote; equal right to stand for election; equal right to hold office.

**Universal adult franchise:** All adult citizens have the right to vote regardless of wealth, gender, or caste. India adopted this in 1950.

## Economic Equality

Economic equality aims to reduce economic disparities — ensuring basic needs are met for all citizens and that wealth is not concentrated in few hands.

**Approaches:**
- Progressive taxation
- Social security and welfare programmes
- Land reform
- Public education and healthcare

**Note:** Perfect economic equality may not be achievable, but reducing extreme inequality is a political goal.

## Equality and Discrimination

Discrimination means treating people unfairly based on characteristics like caste, race, sex, religion, or disability.

**Forms of discrimination:**
- Direct discrimination (explicit)
- Indirect discrimination (neutral rules with discriminatory impact)

**Affirmative action (Positive discrimination):** Deliberate policies to help disadvantaged groups (reservations in India for SC/ST/OBC).

**Debate:** Is treating groups differently to achieve equality itself a violation of formal equality? This is the tension between formal and substantive equality.

---

*AHSEC HS 1st Year Political Science – Equality chapter.*
""",
    ),

    (
        "Social Justice",
        [
            "What is Justice",
            "Just Distribution of Resources",
            "John Rawls and Justice",
            "Rights-Based Approach",
            "Pursuing Social Justice",
        ],
        """# Social Justice

## What is Justice

Justice refers to fair and moral treatment of individuals and groups in society. It involves:

- Giving each person their due
- Fair distribution of benefits and burdens
- Protecting individual rights
- Correcting wrongs

**Types of Justice:**
- **Social Justice:** Fair treatment for all social groups
- **Economic Justice:** Fair distribution of wealth and resources
- **Political Justice:** Equal political rights
- **Legal Justice:** Equal protection under law

## Just Distribution of Resources

How should a society's resources be distributed? Different theories give different answers:

**Libertarian View (Robert Nozick):** Justice means respecting individual property rights. People are entitled to what they earn. Redistribution by the state is unjust.

**Utilitarian View:** Just distribution is one that maximises total welfare/happiness of society. Redistributive policies are justified if they increase overall happiness.

**Egalitarian View:** Resources should be distributed equally (or inequalities must benefit the least advantaged).

**Marxist View:** "From each according to his ability, to each according to his need."

## John Rawls and Justice

John Rawls (A Theory of Justice, 1971) proposed principles of justice from behind a **"veil of ignorance"** — a hypothetical original position where no one knows their place in society (rich/poor, born in what family, what natural talents).

**Rawls' Two Principles of Justice:**

1. **Equal Liberty Principle:** Each person has equal basic liberties (freedom of speech, religion, etc.)

2. **Difference Principle:** Social and economic inequalities are just only if they benefit the most disadvantaged members of society.

**Implication:** Rawls justifies some inequalities (e.g., higher pay for doctors) only if they ultimately benefit the worst-off group.

## Rights-Based Approach

The rights-based approach argues that justice requires protecting fundamental human rights. These rights set limits on what governments can do in the name of welfare or efficiency.

**Key rights:** Right to life, right to liberty, right to equality, right to education.

## Pursuing Social Justice

**In practice:**
- Affirmative action / reservations
- Progressive taxation and welfare state
- Anti-discrimination laws
- Land reforms
- Universal healthcare and education

**In India:**
- Reservations for SC/ST/OBC (Articles 15, 16)
- MGNREGA (guaranteed employment)
- Right to Education Act
- Public Distribution System (PDS)

---

*AHSEC HS 1st Year Political Science – Social Justice chapter.*
""",
    ),

    (
        "Rights",
        [
            "What are Rights",
            "Why do We Need Rights",
            "Legal Rights and the State",
            "Natural Rights",
            "Human Rights",
            "Rights in the Indian Constitution",
        ],
        """# Rights

## What are Rights

Rights are claims that individuals make on society and on the state. They define the space within which individuals can act freely and without interference.

**Characteristics of rights:**
1. Rights are claims, not mere freedoms
2. Rights imply corresponding duties
3. Rights are social — they exist within a community
4. Rights are recognised and enforced by law (legal rights) or by moral consensus

## Why do We Need Rights

1. **Essential for human dignity:** Rights protect individuals from exploitation and oppression
2. **Enable development:** Rights allow people to develop their capacities
3. **Foundation of democracy:** Political rights enable participation in governance
4. **Prevent tyranny:** Rights constrain government power
5. **Social harmony:** Rights-based order reduces conflict

## Legal Rights and the State

Legal rights are rights granted by the state through legislation. They are enforceable in courts.

**Types:**
- **Civil Rights:** Freedom of movement, right to property, right to contract
- **Political Rights:** Right to vote, right to stand for election
- **Social Rights:** Right to education, healthcare, social security

**The state both grants and protects legal rights.** Without state recognition and enforcement, rights remain mere moral claims.

## Natural Rights

Natural rights are rights that all human beings possess by virtue of their human nature — not granted by any government. They are universal, inalienable, and pre-political.

**Key thinkers:**
- **John Locke:** Natural rights are life, liberty, and property
- **Thomas Jefferson (American Declaration of Independence):** "Life, liberty and the pursuit of happiness"

**Critique:** Natural rights are difficult to define and may reflect particular cultural values.

## Human Rights

Human rights are rights that belong to every person regardless of nationality, race, sex, or religion. They are universal, inalienable, and indivisible.

**Universal Declaration of Human Rights (UDHR, 1948):** Adopted by the UN General Assembly after WWII. Lists fundamental rights including:
- Right to life, liberty, and security
- Freedom from torture
- Right to education
- Right to freedom of thought

**International Covenants:**
- International Covenant on Civil and Political Rights (ICCPR)
- International Covenant on Economic, Social and Cultural Rights (ICESCR)

## Rights in the Indian Constitution

The Indian Constitution (Part III, Articles 12-35) guarantees **Fundamental Rights:**

1. **Right to Equality (Articles 14-18):** Equality before law, prohibition of discrimination
2. **Right to Freedom (Articles 19-22):** Speech, assembly, movement, profession, life and liberty
3. **Right Against Exploitation (Articles 23-24):** Prohibition of forced labour, child labour
4. **Right to Freedom of Religion (Articles 25-28)**
5. **Cultural and Educational Rights (Articles 29-30)**
6. **Right to Constitutional Remedies (Article 32):** Right to approach Supreme Court (Dr. Ambedkar called this "heart and soul" of the Constitution)

---

*AHSEC HS 1st Year Political Science – Rights chapter.*
""",
    ),

    (
        "Citizenship",
        [
            "What is Citizenship",
            "Who is a Citizen",
            "Active Citizenship",
            "Citizenship and Nationality",
            "Global Citizenship",
        ],
        """# Citizenship

## What is Citizenship

Citizenship is a legal and political status that entitles individuals to rights and imposes duties within a political community (usually a state).

**Three components:**
1. **Civil citizenship:** Civil rights (equality before law, freedom of speech)
2. **Political citizenship:** Political rights (vote, hold office)
3. **Social citizenship:** Social rights (education, healthcare, social security)

T.H. Marshall (sociologist) traced the evolution of citizenship through these three stages in modern democracies.

## Who is a Citizen

Criteria for citizenship vary by country:

**By birth (jus soli):** Anyone born in the country is a citizen (USA, UK traditionally)

**By descent (jus sanguinis):** Citizenship based on parentage (Germany, Japan)

**By naturalisation:** Foreign nationals can become citizens after meeting requirements (residence period, language test, oath of allegiance)

**In India (Articles 5-11 and Citizenship Act 1955):**
- By birth (born in India before 1987: automatic; after 2003: both parents must be citizens)
- By descent
- By registration or naturalisation

## Active Citizenship

Active citizenship means participating actively in the political and civic life of the community, beyond just holding formal citizenship.

**Forms:**
- Voting in elections
- Standing for office
- Joining political parties or civil society organisations
- Paying taxes
- Community service
- Informed civic discourse

**Democratic citizenship requires active participation** — democracy cannot function with purely passive citizens.

## Citizenship and Nationality

**Nationality** is the legal relationship between a person and a nation-state (often same as citizenship in modern states).

**Distinction:**
- A person can have nationality without being a citizen (some rights denied)
- Citizens usually have full rights; nationals may have restricted rights
- **Example:** British Overseas Nationals — British nationality but not full citizenship rights

**Dual citizenship:** Some countries allow citizenship in two states simultaneously (USA allows this; India does not — though it has Overseas Citizen of India status).

## Global Citizenship

Global citizenship is the idea that individuals have rights and responsibilities that extend beyond national borders, as members of a global community.

**Basis:**
- Universal human rights apply to all
- Global problems (climate change, terrorism, pandemics) require global cooperation
- International organisations (UN, WTO) create global governance

**Critique:** Global citizenship lacks enforcement mechanisms; national citizenship remains primary. Tension between global and national identity.

---

*AHSEC HS 1st Year Political Science – Citizenship chapter.*
""",
    ),

    (
        "Nationalism",
        [
            "What is Nationalism",
            "Elements of Nationalism",
            "Self-Determination",
            "Nation-State",
            "Critiques of Nationalism",
        ],
        """# Nationalism

## What is Nationalism

Nationalism is a political ideology that holds that the nation is the fundamental unit of human political organisation, and that each nation should govern itself (national self-determination).

**Nationalism emerged** with the French Revolution (1789) and spread through 19th-century Europe, leading to the unification of Italy and Germany and decolonisation movements in Asia and Africa.

## Elements of Nationalism

1. **Shared history and culture:** A common heritage, language, and cultural traditions
2. **Common language:** Often seen as the unifying force of a nation
3. **Shared territory:** A homeland with which people identify
4. **Common political goals:** Aspiration for self-governance or national independence
5. **Sense of solidarity:** "We feeling" — a sense of belonging to a group

**Ernest Renan (1882, "What is a Nation?"):** A nation is a "daily plebiscite" — held together by a shared will to live together, not just by race or language.

## Self-Determination

The principle of self-determination holds that nations have the right to determine their own political status and governance.

**In international law:** Article 1 of the UN Charter recognises the right of peoples to self-determination.

**Applications:**
- Decolonisation of Asia and Africa (1940s-1960s)
- Independence movements (Kosovo, East Timor, South Sudan)
- Indian independence from British rule (1947)

**Tension:** Self-determination can conflict with territorial integrity of existing states (e.g., Kashmir, separatist movements).

## Nation-State

A nation-state is a political unit in which the boundaries of the state (political entity) coincide with the boundaries of the nation (cultural/ethnic entity).

**Ideal concept:** One nation = one state. In practice, most states are multi-ethnic and multi-national (India, USA).

**India is a "state-nation" (Stepan/Linz):** A multi-national democratic state that accommodates diverse identities within a shared political framework.

## Critiques of Nationalism

1. **Exclusionary:** Nationalist movements often exclude minorities (ethnic cleansing, genocide)
2. **Leads to conflict:** Nation-states compete; nationalism has caused wars (WWI, WWII)
3. **Chauvinism:** Extreme nationalism (chauvinism) promotes hatred of other nations
4. **Undermines global cooperation:** Nationalism conflicts with international governance (UN, WTO)
5. **Homogenising:** Imposes majority culture on minorities (linguistic nationalism suppresses minority languages)

**Rabindranath Tagore's critique:** Tagore warned against narrow nationalism; advocated universal humanism.

---

*AHSEC HS 1st Year Political Science – Nationalism chapter.*
""",
    ),
]


# ─── History HS 1st Year – AHSEC Arts ────────────────────────────────────────

HISTORY_CHAPTERS = [
    (
        "Writing and City Life",
        [
            "Rise of Cities",
            "Mesopotamian Civilisation",
            "Urbanisation in Mesopotamia",
            "Development of Writing",
            "Scribes and Literacy",
            "Trade and Economy in Mesopotamia",
        ],
        """# Writing and City Life

## Rise of Cities

Cities emerged as a result of surplus agricultural production, specialisation of labour, and the need for administration. The earliest cities appeared around 3500 BCE in Mesopotamia (modern Iraq).

**Conditions for urbanisation:**
- Agricultural surplus (food for non-farmers)
- Trade and commerce
- Administrative needs
- Religious centres (temples)
- Defence requirements (fortifications)

## Mesopotamian Civilisation

Mesopotamia (Greek: "Land between rivers") was the region between the Tigris and Euphrates rivers. It is considered one of the world's earliest civilisations.

**Key cities:** Ur, Uruk, Babylon, Nippur, Akkad.

**Periods:**
- **Sumerian Period (3500-2350 BCE):** City-states like Ur and Uruk
- **Akkadian Empire (2350-2200 BCE):** First empire, under Sargon of Akkad
- **Neo-Sumerian Period (2100-2000 BCE):** Ur III dynasty
- **Babylonian Period (1900-600 BCE):** Hammurabi's Code (1754 BCE)

**Society:** Divided into free citizens, dependent labourers, and slaves. Priests and kings held power.

## Urbanisation in Mesopotamia

Mesopotamian cities were temple-centred. The **Ziggurat** (stepped temple-tower) was the architectural and religious heart of the city.

**Economic basis:** Irrigation agriculture (wheat, barley), crafts, trade.

**Urban features:**
- Specialised workers (potters, weavers, metalworkers)
- Market places
- Administrative scribes
- Walls and gates

## Development of Writing

Writing developed in Mesopotamia around 3200 BCE as a record-keeping tool for trade and temple administration.

**Cuneiform script:**
- Earliest form: pictographic (picture-symbols)
- Evolved into cuneiform (wedge-shaped marks on clay tablets)
- Made using a reed stylus on wet clay tablets
- Tablets were dried/baked for preservation

**Stages of writing development:**
1. Pictographs (3200 BCE): simple pictures of objects
2. Ideographs: pictures representing ideas
3. Phonetic signs: sounds represented by symbols
4. Alphabetic writing (much later in West Asia)

## Scribes and Literacy

**Scribes** were trained writing specialists. They were essential for administration, law, commerce, and religious texts.

- Scribes trained in schools called **edubba** ("tablet houses")
- Curriculum included writing, arithmetic, surveying, music
- Only a small elite could read and write

**Important Mesopotamian texts:**
- **Epic of Gilgamesh:** World's oldest literary work (flood narrative similar to Biblical Noah story)
- **Hammurabi's Code:** One of earliest written law codes (282 laws engraved on stele)
- Temple accounts, land records, commercial contracts

## Trade and Economy in Mesopotamia

Mesopotamia lacked timber, metals, and stone — all had to be imported.

**Trade networks:** Extended to Anatolia (Turkey), Iran, the Indus Valley, and Egypt.

**Exports:** Agricultural products (grain, dates), textiles, pottery.
**Imports:** Timber (cedar from Lebanon), copper (from Oman), gold (from Egypt), semi-precious stones.

**Merchants (tamkārum):** Professional traders who operated on behalf of temples and palaces.

**Silver used as medium of exchange** (standard weight: the shekel).

---

*AHSEC HS 1st Year History – Writing and City Life (Theme 2 based on NCERT Class 11 Themes in World History).*
""",
    ),

    (
        "An Empire Across Three Continents",
        [
            "The Roman Empire",
            "Economy and Society of the Roman Empire",
            "Religion in the Roman Empire",
            "Slavery and Roman Society",
            "Decline of the Roman Empire",
            "The Late Roman Empire",
        ],
        """# An Empire Across Three Continents

## The Roman Empire

The Roman Empire at its height (1st-2nd century CE) stretched across three continents — Europe, Africa (North Africa), and Asia (West Asia) — from Britain in the northwest to Mesopotamia in the east.

**Key figures:**
- **Augustus Caesar (27 BCE – 14 CE):** First Roman Emperor; brought peace (Pax Romana)
- **Trajan (98-117 CE):** Empire at its greatest extent
- **Hadrian (117-138 CE):** Consolidated frontiers; built Hadrian's Wall in Britain

**Capital:** Rome; later Constantinople (founded 330 CE by Emperor Constantine).

## Economy and Society of the Roman Empire

**Economy:**
- Agrarian base (grain, olive oil, wine were main products)
- Long-distance trade: Mediterranean trade connected Rome with India (spices, silk), East Africa (ivory, gold)
- **Currency:** Standardised gold and silver coinage (denarius)
- **Roads:** Over 85,000 km of paved roads for military and commercial movement

**Society:**
- **Senatorial class:** Wealthy landowners, governed provinces
- **Equestrian class:** Wealthy merchants and administrators
- **Free citizens (plebeians):** Farmers, artisans, traders
- **Freedmen:** Former slaves who gained freedom
- **Slaves:** Large proportion of the workforce

## Religion in the Roman Empire

**Polytheistic religion:** Romans worshipped many gods (Jupiter, Mars, Venus, Neptune — adapted from Greek religion).

**Emperor worship:** Emperors were considered divine; emperor cult unified the empire.

**Christianity:**
- Jesus Christ (c. 4 BCE – 30 CE) preached in Judaea (Roman province)
- Christianity spread throughout the empire despite persecution (Nero, Diocletian)
- **313 CE:** Edict of Milan — Emperor Constantine granted freedom of worship to Christians
- **380 CE:** Christianity became official religion of Roman Empire (Theodosius I)

## Slavery and Roman Society

**Scale:** Slaves constituted 30-40% of Italy's population at the height of the Empire.

**Sources of slaves:** Conquest (prisoners of war), piracy, birth (children of slaves).

**Functions:** Agricultural labour (latifundia — large slave estates), domestic service, skilled crafts, mining (harshest conditions), gladiatorial combat.

**Slave revolts:**
- **Spartacus Revolt (73-71 BCE):** Led by slave Spartacus; 70,000 slaves; crushed by Crassus and Pompey.

**Freedmen:** Freed slaves could become Roman citizens; some rose to prominence as merchants, artisans, and imperial administrators.

## Decline of the Roman Empire

**Multiple causes:**
1. **Military:** Constant wars on borders; reliance on barbarian mercenaries; loss of discipline
2. **Economic:** Heavy taxation, debasement of currency, falling agricultural productivity, decline of trade
3. **Political:** Political instability — 235-284 CE ("Crisis of the Third Century"); 50+ emperors in 50 years
4. **Disease:** Antonine Plague (165-180 CE) and Plague of Cyprian (249-262 CE) killed millions
5. **External pressures:** Migrations and invasions of Germanic tribes (Visigoths, Vandals, Huns)

## The Late Roman Empire

**Diocletian (284-305 CE):** Divided empire into Eastern and Western halves for administrative efficiency.

**Constantine (306-337 CE):** Founded Constantinople (modern Istanbul) as eastern capital; converted to Christianity.

**Final division (395 CE):** Empire permanently split into:
- **Western Roman Empire** (capital: Ravenna/Rome) — fell 476 CE (Romulus Augustulus deposed by Odoacer)
- **Eastern Roman Empire (Byzantine)** (capital: Constantinople) — survived until 1453 CE

**476 CE: Fall of Western Roman Empire** — traditional date for end of ancient world and beginning of Middle Ages in Europe.

---

*AHSEC HS 1st Year History – The Roman Empire (Theme 3, NCERT Class 11 Themes in World History).*
""",
    ),

    (
        "Nomadic Empires",
        [
            "The Mongols and their World",
            "Genghis Khan and the Mongol Empire",
            "Nature of Mongol Rule",
            "Trade and Communication in Mongol Empire",
            "Significance of Mongol Empire",
        ],
        """# Nomadic Empires

## The Mongols and their World

The Mongols were pastoral nomads of the Central Asian steppes. Their lifestyle was organised around:

- **Herding:** Cattle, horses, sheep, goats
- **Mobility:** Moved seasonally following pasture
- **Horses:** Central to their military power
- **Ger (Yurt):** Portable felt tent as dwelling

**Social organisation:** Clans and tribes; kinship was the basis of loyalty. Alliances and conflicts between tribes were common before Mongol unification.

## Genghis Khan and the Mongol Empire

**Genghis Khan (born Temujin, c. 1162-1227):**
- Born into a minor Mongol clan
- Lost his father to poison by rivals; endured poverty and captivity
- Through military genius and political skill, united all Mongol tribes by 1206
- Proclaimed **Genghis Khan** ("Universal Ruler") at a great assembly (kuriltai) in 1206

**Conquests:**
- **China:** Conquered northern China (Jin dynasty), also attacked Song China
- **Central Asia:** Conquered Khwarazm Shah's empire (modern Uzbekistan, Iran, Afghanistan)
- **Eastern Europe:** Mongols reached Poland and Hungary (1241)

By his death in 1227, Genghis Khan ruled the largest contiguous empire in history.

**Military innovations:**
- Highly mobile cavalry tactics
- Psychological warfare and terror
- Adopted siege technology from Chinese and Persian engineers
- Sophisticated military organisation (decimal system: groups of 10, 100, 1000, 10,000)

## Nature of Mongol Rule

After Genghis Khan's death, the empire was divided among his sons into four **khanates:**
1. **Golden Horde** (Russia/Central Asia)
2. **Chagatai Khanate** (Central Asia)
3. **Ilkhanate** (Persia/Iran)
4. **Khanate of the Great Khan** (China/Mongolia) → ruled by Kublai Khan

**Governance:**
- Initially destructive — cities destroyed, populations massacred (Baghdad 1258)
- Later, Mongols adopted local cultures and administrative systems
- Meritocracy — capable administrators from any background appointed
- Religious tolerance — Mongols generally tolerated all religions

**Kublai Khan (1260-1294):** Ruled China, founded Yuan dynasty; promoted trade and welcomed foreign visitors (Marco Polo visited his court).

## Trade and Communication in Mongol Empire

The Mongol Empire created the **Pax Mongolica** (Mongol Peace) — a period of relative stability and security across Eurasia that facilitated:

**Silk Road revival:** Long-distance trade between China and Europe flourished. Merchants, diplomats, and missionaries could travel safely.

**Relay stations (Yam system):** A postal relay system with fresh horses every 40 km enabled rapid communication and allowed merchants to travel 400+ km per day.

**Cultural exchange:**
- Chinese technology (printing, gunpowder, paper money) spread westward
- Islamic astronomy and mathematics reached China and Europe
- Bubonic plague (Black Death) also spread along Mongol trade routes (1340s)

## Significance of Mongol Empire

1. **Connected East and West:** The Silk Road's safety under Mongol rule connected China, Central Asia, Iran, and Europe.

2. **Cross-cultural exchange:** Unprecedented movement of people, goods, and ideas.

3. **Transmission of knowledge:** Chinese printing and gunpowder reached Europe; Islamic science reached China.

4. **Unintended consequences:** Black Death (1347-1353) may have travelled via Mongol trade routes — killed 30-60% of Europe's population.

5. **Political impact:** Destruction of Abbasid Caliphate (1258) ended Baghdad's golden age; Mongol invasions reshaped Central Asian political landscape.

---

*AHSEC HS 1st Year History – Nomadic Empires (Theme 5, NCERT Class 11 Themes in World History).*
""",
    ),

    (
        "The Three Orders",
        [
            "Feudalism in Europe",
            "The Three Orders of Society",
            "Serfdom and Manor System",
            "The Church in Feudal Society",
            "Towns and Trade in Medieval Europe",
            "Decline of Feudalism",
        ],
        """# The Three Orders

## Feudalism in Europe

Feudalism was the social, economic, and political system that dominated Western Europe from about the 9th to 15th centuries. It arose after the collapse of the Carolingian Empire.

**Origins:** Raids by Vikings, Magyars, and Saracens in the 9th-10th centuries forced local populations to seek protection from powerful lords → decentralised authority.

**Key features:**
- Hierarchy of lords and vassals
- Land grants (fiefs) in exchange for military service
- Manorial (serfdom-based) agricultural economy
- Church as a unifying institution

## The Three Orders of Society

Medieval European society was ideally conceived as three complementary orders:

1. **Those who pray (oratores):** Clergy — monks, priests, bishops; responsible for spiritual welfare
2. **Those who fight (bellatores):** Knights and nobles; responsible for military protection
3. **Those who work (laboratores):** Peasants/serfs; responsible for agricultural production

**Reality:** This was an idealised scheme; in practice, nobles also controlled land and exploited peasants; clergy owned vast estates; merchants (a fourth group) grew in importance by the 12th-13th century.

## Serfdom and Manor System

**The Manor** was the basic economic unit of feudal Europe — a lord's estate, typically including:
- The lord's residence (manor house or castle)
- Peasant villages
- Common land (forests, pastures)
- Parish church

**Serfs:** The majority of the rural population. They were bound to the land (cannot leave without lord's permission), obliged to provide:
- **Labour service:** Working on lord's land several days per week
- **Dues:** Portion of harvest as rent
- **Fees:** Payment for use of lord's mill, oven, and other facilities

**Serfs were not slaves:** They had customary rights — strips of land, access to commons, could not be killed arbitrarily.

## The Church in Feudal Society

The **Catholic Church** was the most powerful institution in medieval Europe:

- **Universal authority:** Pope claimed supremacy over all Christian kings
- **Land ownership:** Church owned up to 1/3 of agricultural land in some regions
- **Education:** Monasteries and cathedral schools were centres of learning
- **Social welfare:** Ran hospitals, orphanages, and poorhouses
- **Cultural unifier:** Latin as the common language of scholarship and worship

**Key events:**
- **Investiture Controversy (1076-1122):** Struggle between Pope and Holy Roman Emperor over appointment of church officials → Pope Gregory VII vs. Emperor Henry IV → Pope's victory established Church supremacy
- **Crusades (1095-1291):** Military expeditions to recapture Holy Land; mixed results; increased trade with Middle East

## Towns and Trade in Medieval Europe

From the 10th-11th century, European towns began to grow again after centuries of decline.

**Causes of urban revival:**
- Agricultural surplus (from new techniques: three-field system, heavy plough, horse collar)
- Long-distance trade (especially Italian city-states with Middle East)
- Demand for crafts and specialised goods

**Craft guilds:** Associations of craftspeople (weavers, blacksmiths, bakers) — regulated quality, training (apprentices → journeymen → masters), and prices.

**Fairs:** Important commercial events — Champagne Fairs in France were major European trading events.

**Italian city-states:** Venice, Genoa, Florence became great commercial centres trading with Byzantine Empire and Islamic world.

## Decline of Feudalism

**Causes:**
1. **Black Death (1347-1353):** Killed 30-60% of European population; labour became scarce → serfs could demand better terms → weakened serfdom
2. **Peasant revolts:** English Peasants' Revolt (1381), French Jacquerie (1358) challenged feudal hierarchy
3. **Hundred Years' War (1337-1453):** Disrupted feudal military organisation; rise of professional armies
4. **Growth of towns and commerce:** Merchants accumulated wealth independent of land-ownership
5. **Rise of centralised monarchies:** Kings gradually reduced power of feudal lords

**Legacy:** Feudalism's decline set the stage for the Renaissance, Reformation, and eventually capitalism.

---

*AHSEC HS 1st Year History – The Three Orders (Feudalism in Medieval Europe, Theme 6 NCERT Class 11).*
""",
    ),

    (
        "Changing Cultural Traditions",
        [
            "The Renaissance",
            "Humanism and Classical Learning",
            "Renaissance Art and Architecture",
            "The Reformation",
            "Counter-Reformation",
            "Print Culture and its Impact",
        ],
        """# Changing Cultural Traditions

## The Renaissance

The Renaissance (French: "rebirth") was a cultural and intellectual movement that began in Italy in the 14th century and spread across Europe by the 16th century. It represented a renewed interest in the classical learning of ancient Greece and Rome.

**Origins:** Italy — particularly Florence, Venice, and Rome. Why Italy?
- Remnants of Roman heritage were physically present
- Wealthy city-states (Medici family in Florence) patronised arts
- Trade with Byzantine Empire (which had preserved Greek texts) and the Islamic world

**Key features:**
- Emphasis on human potential (humanism)
- Secular approach to knowledge
- Revival of classical art and architecture
- Scientific curiosity and observation

## Humanism and Classical Learning

**Humanism** placed human beings (not God or the Church) at the centre of intellectual inquiry. Humanists:
- Studied Greek and Latin classical texts
- Valued reason, individualism, and earthly achievements
- Believed education should develop the whole person (mind, body, morals)

**Key humanists:**
- **Francesco Petrarch (1304-1374):** "Father of Humanism"; revived Latin literature
- **Desiderius Erasmus (1469-1536):** Christian humanist; criticised Church corruption; *In Praise of Folly*
- **Leonardo Bruni (1370-1444):** History of Florence; civic humanism

## Renaissance Art and Architecture

**Visual arts:**
- **Perspective:** Linear perspective (3D illusion on 2D surface) — Brunelleschi, Alberti
- **Anatomical realism:** Leonardo da Vinci's anatomical drawings
- **Secular subjects:** Not just religious themes; portraits, mythological scenes

**Key artists:**
- **Leonardo da Vinci (1452-1519):** *Mona Lisa*, *The Last Supper*; painter, scientist, engineer
- **Michelangelo (1475-1564):** *David*, *Pietà*, Sistine Chapel ceiling
- **Raphael (1483-1520):** *School of Athens*
- **Botticelli (1445-1510):** *Birth of Venus*

**Architecture:**
- Revival of classical elements: columns, arches, domes
- **Brunelleschi:** Florence Cathedral dome
- **St. Peter's Basilica, Rome:** Michelangelo's dome

## The Reformation

The Protestant Reformation was a 16th-century movement to reform the Catholic Church. It ultimately split Western Christianity.

**Causes:**
- Church corruption: sale of indulgences, nepotism, worldly bishops
- Humanist criticism of Church
- Rising nationalism (German princes resented Papal authority)
- Printing press spread reforming ideas rapidly

**Key figures:**
- **Martin Luther (1483-1546):** German monk; nailed 95 Theses to Wittenberg church door (1517); challenged sale of indulgences and Papal authority; translated Bible into German
- **John Calvin (1509-1564):** Geneva; doctrine of predestination; established theocratic government
- **Henry VIII of England:** Broke with Rome over divorce; established Church of England (Anglicanism)

**Result:** Europe split between Catholics and Protestants; Wars of Religion followed.

## Counter-Reformation

The Counter-Reformation (Catholic Reformation) was the Church's response to Protestantism:

- **Council of Trent (1545-1563):** Reformed Church abuses while reaffirming Catholic doctrine
- **Society of Jesus (Jesuits):** Founded by Ignatius of Loyola (1540); educated elite; missionary work in Americas, Asia (including India — Francis Xavier)
- **Inquisition:** Strengthened to combat heresy
- **Index of Forbidden Books:** List of banned publications

## Print Culture and its Impact

**Johannes Gutenberg (c. 1450):** Invented movable type printing press in Germany. This revolutionised communication.

**Impact:**
1. **Spread of Reformation:** Luther's ideas spread rapidly through printed pamphlets and books
2. **Vernacular languages:** Bible translated into local languages → literacy spread; Latin's monopoly broken
3. **Scientific Revolution:** Scientific knowledge could be shared and built upon rapidly
4. **Standardisation of languages:** Print standardised spelling and grammar
5. **Rise of public opinion:** Printing enabled criticism of Church and state

**Book production:** Before printing: 20 million manuscripts in Europe; by 1500: 20 million printed books; by 1600: 200 million.

---

*AHSEC HS 1st Year History – Changing Cultural Traditions (Renaissance and Reformation, Theme 7 NCERT Class 11).*
""",
    ),

    (
        "Displacing Indigenous Peoples",
        [
            "European Colonisation of North America",
            "Indigenous Peoples of North America",
            "Colonisation and Disease",
            "Resistance and Subjugation",
            "Impact on Indigenous Culture",
        ],
        """# Displacing Indigenous Peoples

## European Colonisation of North America

European powers — Spain, France, England, and the Netherlands — colonised North America from the late 15th century onwards.

**Timeline:**
- **1492:** Columbus reaches the Americas (Caribbean)
- **1607:** First permanent English settlement at Jamestown, Virginia
- **1620:** Pilgrim Fathers found Plymouth Colony, Massachusetts
- **17th-18th century:** Thirteen British colonies established along the eastern seaboard

**Motivations for colonisation:**
- Economic: Trade, resources (fur, timber, land, gold)
- Religious: Puritan settlers fleeing religious persecution in England
- Political: Territorial expansion and rivalry between European powers

## Indigenous Peoples of North America

Before European arrival, North America was home to diverse Native American nations, each with distinct cultures, languages, and social organisations.

**Major groups:**
- **Northeast:** Iroquois Confederacy (Haudenosaunee) — highly developed political confederation
- **Southeast:** Cherokee, Choctaw, Creek
- **Plains:** Sioux, Cheyenne, Comanche — nomadic bison hunters (post-horse)
- **Southwest:** Navajo, Hopi — agriculture in desert
- **Northwest Coast:** Haida — fishing societies; totem poles

**Population (pre-contact):** Estimates range from 7 to 18 million people in North America.

## Colonisation and Disease

**Devastating epidemics** were the most catastrophic consequence of European contact. Indigenous peoples had no immunity to European diseases.

**Diseases:** Smallpox, measles, influenza, typhus.

**Mortality:** Estimates suggest 50-90% of indigenous populations died within a century of contact. Some regions lost 90%+ of their population.

**Smallpox:** The most deadly. Used sometimes deliberately (infected blankets given to Native Americans by British forces in 1763 — documented biological warfare).

**Demographic collapse:** Weakened indigenous societies, making them more vulnerable to military conquest and dispossession.

## Resistance and Subjugation

Despite enormous suffering, indigenous peoples resisted colonisation through:

**Military resistance:**
- **King Philip's War (1675-78):** Metacom (Wampanoag) led alliance against New England colonies; one of bloodiest colonial wars
- **Pontiac's War (1763):** Great Lakes tribes attacked British forts
- **Battle of Little Bighorn (1876):** Sioux and Cheyenne defeated US 7th Cavalry (Custer's Last Stand)

**Legal and diplomatic resistance:**
- Treaties (though most were violated by the US government)
- Appeals to courts (Cherokee Nation vs. Georgia, 1831)

**Policies of subjugation:**
- Indian Removal Act (1830): Forced removal of Five Civilised Tribes to "Indian Territory" (Oklahoma) — **Trail of Tears** — 4,000+ Cherokee deaths
- Reservation system: Confined indigenous peoples to restricted land
- Boarding schools: Forcibly separated children from families; banned indigenous languages

## Impact on Indigenous Culture

**Cultural destruction:**
- Loss of land → loss of traditional economies (hunting, fishing, farming)
- Banning of traditional religions, ceremonies, languages in 19th-20th centuries
- Boarding schools: "Kill the Indian, save the man" — forced assimilation policy

**Legacy:**
- Today, about 2% of the US population identifies as Native American (5.2 million people)
- Reservations remain among the poorest communities in America
- Ongoing struggles for land rights, sovereignty, and cultural preservation
- Recognition of historical injustice: US government formal apologies in recent decades

---

*AHSEC HS 1st Year History – Displacing Indigenous Peoples (Theme 10, NCERT Class 11 Themes in World History).*
""",
    ),

    (
        "Paths to Modernisation",
        [
            "Modernisation and Different Paths",
            "Meiji Restoration in Japan",
            "Japan's Industrialisation",
            "China's Response to Modernisation",
            "Nationalism in East Asia",
        ],
        """# Paths to Modernisation

## Modernisation and Different Paths

Modernisation refers to the process of social, economic, and political transformation associated with industrialisation, urbanisation, scientific rationalism, and democracy. Different societies took different paths to modernisation.

**Key features of modernisation:**
- Industrialisation (shift from agriculture to manufacturing)
- Urbanisation (people moving to cities)
- Scientific and technological development
- Democratic governance
- Secular education

**In the 19th-20th centuries, Asian nations faced the challenge of modernising to resist Western colonial dominance.**

## Meiji Restoration in Japan

**Background:**
- Japan was under Tokugawa Shogunate (1600-1868) — isolated from the world (sakoku policy)
- 1853: US Commodore Matthew Perry's "Black Ships" forced Japan to open ports (unequal treaties)
- Crisis of sovereignty → overthrow of Shogunate

**Meiji Restoration (1868):**
- Emperor Meiji (1868-1912) restored to power
- Slogan: "Enrich the Country, Strengthen the Military" (fukoku kyōhei)
- Slogan: "Civilisation and Enlightenment" (bunmei kaika)

**Reforms:**
- **Political:** Centralised modern state; Meiji Constitution (1889); elected parliament (Diet)
- **Economic:** Industrialisation led by government; railways, telegraph, modern industries
- **Military:** Universal conscription; modern army and navy (German model for army, British for navy)
- **Education:** Universal primary education; modern universities; sent students abroad
- **Social:** Western dress, calendars, customs adopted by elite; feudal class system abolished

## Japan's Industrialisation

Japan industrialised rapidly from the 1870s-1890s, becoming Asia's first industrial power.

**Government role:**
- Founded model factories
- Subsidised key industries
- Built infrastructure (railways, ports)
- Educated technical workforce

**Zaibatsu:** Large family-owned industrial conglomerates (Mitsubishi, Mitsui, Sumitomo) dominated Japanese industry.

**Results by 1900:**
- Major steel, shipbuilding, textile industries
- Modern railways across Japan
- Powerful army and navy

**Military victories:**
- **Sino-Japanese War (1894-95):** Japan defeated China; gained Taiwan and influence in Korea
- **Russo-Japanese War (1904-05):** Japan defeated Russia — first Asian nation to defeat a European power; gained international recognition

## China's Response to Modernisation

**China's situation:**
- After Opium Wars (1839-42, 1856-60), China was forced to sign unequal treaties
- Concessions to Western powers; humiliating defeats
- Qing dynasty (1644-1912) resisted fundamental reform

**Responses:**
- **Self-Strengthening Movement (1860s-1895):** "Chinese learning for fundamental values, Western learning for practical use" — adopted Western technology but rejected political reform; failed (exposed in 1895 Sino-Japanese War defeat)
- **Hundred Days' Reform (1898):** Emperor Guangxu's radical reforms — blocked by Empress Dowager Cixi after 103 days
- **Boxer Rebellion (1900):** Anti-foreign uprising suppressed by combined Western and Japanese forces

**Fall of Qing Dynasty (1911-12):** Revolution led by Sun Yat-sen; Republic of China established.

## Nationalism in East Asia

**Japanese nationalism:**
- Pan-Asian rhetoric ("Asia for Asians") combined with Japanese imperialism
- Japan portrayed itself as liberating Asia from Western colonialism while actually colonising Korea (1910), China, and Southeast Asia

**Chinese nationalism:**
- Sun Yat-sen's "Three Principles of the People": Nationalism, Democracy, People's Livelihood
- May Fourth Movement (1919): Protest against Paris Peace Conference (Versailles) provisions → rise of modern Chinese nationalism

**Contrast:** Japan industrialised independently and became a colonial power; China struggled against both Western imperialism and internal weakness, eventually undergoing revolution (1911 and 1949).

---

*AHSEC HS 1st Year History – Paths to Modernisation (Theme 11, NCERT Class 11 Themes in World History).*
""",
    ),
]


# ─── Seeder Class ─────────────────────────────────────────────────────────────

class ArtsSeeder:
    def __init__(self, db, dry_run=False, verbose=False, subject_filter=None):
        self.db = db
        self.dry_run = dry_run
        self.verbose = verbose
        self.subject_filter = subject_filter
        self.counts = {k: 0 for k in [
            "boards_found", "classes_found", "streams_found",
            "subjects_found", "subjects_created",
            "chapters_created", "chapters_updated", "chapters_skipped",
            "topics_total",
        ]}

    # ── low-level finders ──────────────────────────────────────────────────

    def find_or_create_board(self, name, slug):
        doc = self.db.boards.find_one({"slug": slug})
        if doc:
            self.counts["boards_found"] += 1
            return doc["_id"]
        if self.dry_run:
            logger.info(f"[DRY] Would create board: {name}")
            return f"dry-board-{slug}"
        now = now_utc()
        r = self.db.boards.insert_one({"name": name, "slug": slug, "status": "active", "created_at": now, "updated_at": now})
        logger.info(f"Created board: {name}")
        return r.inserted_id

    def find_or_create_class(self, name, board_id):
        doc = self.db.classes.find_one({"name": name, "board_id": board_id})
        if doc:
            self.counts["classes_found"] += 1
            return doc["_id"]
        if self.dry_run:
            logger.info(f"[DRY] Would create class: {name}")
            return f"dry-class-{slugify(name)}"
        now = now_utc()
        r = self.db.classes.insert_one({"name": name, "board_id": board_id, "status": "active", "created_at": now, "updated_at": now})
        logger.info(f"Created class: {name}")
        return r.inserted_id

    def find_or_create_stream(self, name, class_id):
        doc = self.db.streams.find_one({"name": name, "class_id": class_id})
        if doc:
            self.counts["streams_found"] += 1
            return doc["_id"]
        if self.dry_run:
            logger.info(f"[DRY] Would create stream: {name}")
            return f"dry-stream-{slugify(name)}"
        now = now_utc()
        r = self.db.streams.insert_one({"name": name, "class_id": class_id, "status": "active", "created_at": now, "updated_at": now})
        logger.info(f"Created stream: {name}")
        return r.inserted_id

    def find_or_create_subject(self, name, stream_id):
        doc = self.db.subjects.find_one({"name": name, "stream_id": stream_id})
        if doc:
            self.counts["subjects_found"] += 1
            return doc["_id"]
        if self.dry_run:
            logger.info(f"[DRY] Would create subject: {name}")
            return f"dry-subject-{slugify(name)}"
        now = now_utc()
        r = self.db.subjects.insert_one({
            "name": name,
            "stream_id": stream_id,
            "status": "active",
            "slug": slugify(name),
            "created_at": now,
            "updated_at": now,
        })
        logger.info(f"Created subject: {name}")
        self.counts["subjects_created"] += 1
        return r.inserted_id

    def upsert_chapter(self, title, subject_id, chapter_number, topics, content_en):
        """Insert or update chapter with full content and published status."""
        slug = slugify(title)
        topic_dicts = [make_topic(t) for t in topics]
        word_count = len(content_en.split()) if content_en else 0
        now = now_utc()

        if self.dry_run:
            logger.info(f"[DRY] Would upsert chapter #{chapter_number}: {title} ({len(topics)} topics, {word_count} words)")
            self.counts["chapters_created"] += 1
            self.counts["topics_total"] += len(topics)
            return f"dry-chapter-{slug}"

        existing = self.db.chapters.find_one({"slug": slug, "subject_id": subject_id})

        doc = {
            "title": title,
            "slug": slug,
            "subject_id": subject_id,
            "chapter_number": chapter_number,
            "status": "published",
            "content_en": content_en,
            "content_as": None,
            "meta_description": f"{title} – AHSEC HS 1st Year comprehensive notes with topic-wise explanations.",
            "keywords": ", ".join(topics[:5]),
            "word_count": word_count,
            "notes_generated": True,
            "published_topics": topic_dicts,
            "faq_jsonld": None,
            "updated_at": now,
        }

        if existing:
            self.db.chapters.update_one({"_id": existing["_id"]}, {"$set": doc})
            self.counts["chapters_updated"] += 1
            if self.verbose:
                logger.info(f"  Updated chapter: {title}")
        else:
            doc["created_at"] = now
            self.db.chapters.insert_one(doc)
            self.counts["chapters_created"] += 1
            if self.verbose:
                logger.info(f"  Created chapter: {title}")

        self.counts["topics_total"] += len(topics)

    def seed_subject_chapters(self, subject_name, chapter_data_list, stream_id):
        subject_id = self.find_or_create_subject(subject_name, stream_id)
        logger.info(f"  Seeding {len(chapter_data_list)} chapters for {subject_name}...")
        for idx, entry in enumerate(chapter_data_list, start=1):
            title, topics, content_en = entry
            self.upsert_chapter(title, subject_id, idx, topics, content_en)
        logger.info(f"  Done: {subject_name}")

    def seed(self):
        logger.info("=" * 60)
        logger.info("AHSEC Arts HS 1st Year Full Content Seeder")
        logger.info("=" * 60)

        board_id = self.find_or_create_board("AHSEC", "ahsec")
        class_id = self.find_or_create_class("HS 1st Year", board_id)
        stream_id = self.find_or_create_stream("Arts", class_id)

        subject_map = {
            "Economics": ECONOMICS_CHAPTERS,
            "Political Science": POLITICAL_SCIENCE_CHAPTERS,
            "History": HISTORY_CHAPTERS,
        }

        for subject_name, chapters in subject_map.items():
            if self.subject_filter and self.subject_filter.lower() != subject_name.lower():
                logger.info(f"Skipping {subject_name} (filter: {self.subject_filter})")
                continue
            logger.info(f"\nSeeding {subject_name}...")
            self.seed_subject_chapters(subject_name, chapters, stream_id)

    def print_summary(self):
        logger.info("\n" + "=" * 60)
        logger.info("SEEDING SUMMARY")
        logger.info("=" * 60)
        for k, v in self.counts.items():
            logger.info(f"  {k.replace('_', ' ').title()}: {v}")
        if self.dry_run:
            logger.info("  (DRY RUN — nothing written to MongoDB)")
        logger.info("=" * 60)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Seed AHSEC Arts HS 1st Year full content into MongoDB")
    parser.add_argument("--mongodb-uri", default=os.environ.get("MONGODB_URI"))
    parser.add_argument("--subject", default=None, help="Seed only one subject: Economics, Political Science, or History")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.dry_run:
        logger.info("DRY RUN mode")
        seeder = ArtsSeeder(db=None, dry_run=True, verbose=args.verbose, subject_filter=args.subject)
        seeder.seed()
        seeder.print_summary()
        return

    if not args.mongodb_uri:
        logger.error("MongoDB URI required. Set MONGODB_URI or pass --mongodb-uri.")
        sys.exit(1)

    logger.info("Connecting to MongoDB...")
    client = MongoClient(args.mongodb_uri)
    db = client[DB_NAME]

    try:
        client.admin.command("ping")
        logger.info(f"Connected to MongoDB (db: {DB_NAME})")
    except Exception as e:
        logger.error(f"Connection failed: {e}")
        sys.exit(1)

    seeder = ArtsSeeder(db=db, dry_run=False, verbose=args.verbose, subject_filter=args.subject)
    seeder.seed()
    seeder.print_summary()
    client.close()
    logger.info("Done.")


if __name__ == "__main__":
    main()
