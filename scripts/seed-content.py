#!/usr/bin/env python3
"""
Seed script for AHSEC Class 11 Arts - Economics content.
Seeds educational content into MongoDB for all chapters in Part A (Microeconomics)
and Part B (Statistics for Economics).

Usage:
    MONGODB_URI="mongodb+srv://..." python3 scripts/seed-content.py
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:
    print("ERROR: motor package is required. Install with: pip install motor")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Content Hierarchy
# ---------------------------------------------------------------------------

HIERARCHY = {
    "board": {"slug": "ahsec", "name": "AHSEC", "type": "board"},
    "stream": {"slug": "arts", "name": "Arts", "board_slug": "ahsec", "type": "stream"},
    "class": {
        "slug": "class-11",
        "name": "Class 11 (HS First Year)",
        "board_slug": "ahsec",
        "type": "class",
    },
    "subject": {
        "slug": "economics",
        "name": "Economics",
        "board_slug": "ahsec",
        "class_slug": "class-11",
        "stream_slug": "arts",
        "type": "subject",
    },
}


# ---------------------------------------------------------------------------
# Chapter definitions with educational content
# ---------------------------------------------------------------------------

CHAPTERS = [
    # ===================================================================
    # Part A: Introductory Microeconomics
    # Unit I: Introduction
    # ===================================================================
    {
        "chapter_number": 1,
        "slug": "introduction-to-economics",
        "title": "Introduction to Economics",
        "description": "An introduction to Economics covering its definition, scope, and the distinction between microeconomics and macroeconomics as studied in AHSEC Class 11.",
        "topics": [
            "What is Economics",
            "Microeconomics",
            "Macroeconomics",
            "Positive and Normative Economics",
            "Central Problems of an Economy",
            "Production Possibility Curve",
        ],
        "keywords": [
            "economics", "scarcity", "microeconomics", "macroeconomics",
            "positive economics", "normative economics", "central problems",
            "production possibility curve", "opportunity cost", "AHSEC class 11",
        ],
        "body_markdown": """# Introduction to Economics

## What is Economics

Economics is the social science that studies how individuals, businesses, governments, and societies make choices about allocating scarce resources to satisfy unlimited wants. The word economics is derived from the Greek word 'Oikonomia' meaning household management.

### Definitions of Economics

- **Adam Smith (Wealth Definition):** Economics is the study of the nature and causes of the wealth of nations. Smith focused on how nations can increase their wealth through production and trade.
- **Alfred Marshall (Welfare Definition):** Economics is the study of mankind in the ordinary business of life. It examines how individuals earn income and how they use it.
- **Lionel Robbins (Scarcity Definition):** Economics is the science which studies human behaviour as a relationship between ends and scarce means which have alternative uses. This is the most widely accepted definition.

### Key Characteristics
- Unlimited wants with limited resources
- Resources have alternative uses
- Every choice involves an opportunity cost
- Economics helps in optimal allocation of resources

## Microeconomics

Microeconomics is the branch of economics that studies the behaviour of individual economic units such as consumers, firms, and industries. It analyzes how these units make decisions regarding the allocation of limited resources.

### Scope of Microeconomics
- **Consumer Behaviour:** How consumers decide what to buy given their income and prices
- **Producer Behaviour:** How firms decide what to produce, how much to produce, and at what price
- **Market Equilibrium:** How prices are determined through the interaction of demand and supply
- **Factor Pricing:** How wages, rent, interest, and profits are determined

### Importance of Microeconomics
- Helps in understanding price determination
- Useful for business decision making
- Provides the basis for welfare economics
- Helps in efficient resource allocation

## Macroeconomics

Macroeconomics studies the economy as a whole. It deals with aggregate economic variables such as national income, total employment, general price level, and economic growth.

### Scope of Macroeconomics
- National income and output
- Employment and unemployment
- Inflation and price level
- Economic growth and development
- Fiscal and monetary policies

### Difference Between Micro and Macroeconomics
| Aspect | Microeconomics | Macroeconomics |
|--------|---------------|----------------|
| Scope | Individual units | Economy as whole |
| Variables | Individual prices, output | Aggregate price level, GDP |
| Objective | Price determination | Income determination |
| Example | Price of rice | National income |

## Positive and Normative Economics

### Positive Economics
Positive economics deals with facts and cause-and-effect relationships. It describes 'what is' without making value judgments. Statements can be verified or tested.
- Example: "If the price of petrol rises, demand will fall."

### Normative Economics
Normative economics involves value judgments about what the economy 'ought to be.' These statements cannot be tested objectively.
- Example: "The government should provide free education to all."

## Central Problems of an Economy

Every economy faces three central problems due to scarcity of resources:

### 1. What to Produce
The economy must decide which goods and services to produce and in what quantities. Since resources are limited, producing more of one good means producing less of another.

### 2. How to Produce
This involves choosing the technique of production:
- **Labour-intensive technique:** Uses more labour relative to capital
- **Capital-intensive technique:** Uses more machinery relative to labour
The choice depends on availability and cost of factors of production.

### 3. For Whom to Produce
This relates to the distribution of goods and services among members of society. It addresses the question of who gets what share of the total output.

## Production Possibility Curve (PPC)

The Production Possibility Curve (also called Production Possibility Frontier) shows the maximum possible combinations of two goods that an economy can produce with given resources and technology.

### Properties of PPC
- It slopes downward from left to right (negative slope) showing the trade-off between two goods
- It is concave to the origin due to increasing opportunity cost
- Points on the curve represent full and efficient utilization of resources
- Points inside the curve represent underutilization of resources
- Points outside the curve are unattainable with current resources

### Opportunity Cost
Opportunity cost is the value of the next best alternative foregone when a choice is made. On the PPC, it is measured as the amount of one good that must be given up to produce an additional unit of another good.

### Shifts in PPC
- **Rightward shift:** Increase in resources or improvement in technology
- **Leftward shift:** Decrease in resources or deterioration of technology
""",
    },
    # ===================================================================
    # Unit II: Consumer's Equilibrium and Demand
    # ===================================================================
    {
        "chapter_number": 2,
        "slug": "consumers-equilibrium",
        "title": "Consumer's Equilibrium",
        "description": "Understanding consumer equilibrium through utility analysis including total utility, marginal utility, and the law of diminishing marginal utility.",
        "topics": [
            "Utility",
            "Total Utility",
            "Marginal Utility",
            "Law of Diminishing Marginal Utility",
            "Consumer Equilibrium (Single Commodity)",
            "Consumer Equilibrium (Two Commodities)",
        ],
        "keywords": [
            "utility", "total utility", "marginal utility", "diminishing marginal utility",
            "consumer equilibrium", "utils", "cardinal utility", "equi-marginal utility",
        ],
        "body_markdown": """# Consumer's Equilibrium

## Utility

Utility refers to the satisfaction or pleasure that a consumer derives from the consumption of a good or service. It is a subjective concept and varies from person to person.

### Characteristics of Utility
- Utility is subjective and psychological
- It varies from person to person, place to place, and time to time
- Utility is not the same as usefulness
- It can be measured in cardinal (numerical) or ordinal (ranking) terms
- Utility has no ethical or moral significance

### Cardinal Utility Approach
The cardinal approach assumes that utility can be measured numerically in units called 'utils.' This approach was developed by Alfred Marshall.

## Total Utility (TU)

Total Utility is the total satisfaction derived from the consumption of all units of a commodity during a given period of time.

### Formula
TU = U1 + U2 + U3 + ... + Un

Where U1, U2, U3... represent utility from 1st, 2nd, 3rd... units respectively.

### Properties
- TU increases at a decreasing rate as consumption increases
- TU reaches a maximum point (called saturation point)
- After saturation, TU begins to decline

## Marginal Utility (MU)

Marginal Utility is the additional utility derived from the consumption of one additional unit of a commodity. It is the change in total utility resulting from a one-unit change in consumption.

### Formula
MU_n = TU_n - TU_(n-1)

Or, MU = Change in TU / Change in quantity consumed

### Relationship between TU and MU
- When MU is positive, TU is increasing
- When MU is zero, TU is at maximum (saturation point)
- When MU is negative, TU is decreasing

## Law of Diminishing Marginal Utility

The Law of Diminishing Marginal Utility states that as a consumer consumes more and more units of a commodity, the utility derived from each successive unit goes on decreasing.

### Assumptions
- The consumer is rational
- The commodity is consumed in standard units
- Consumption is continuous without any time gap
- The quality of the commodity remains constant
- Consumer's taste and preference remain unchanged
- Income of consumer remains constant

### Illustration
| Units | TU (Utils) | MU (Utils) |
|-------|-----------|-----------|
| 1 | 10 | 10 |
| 2 | 18 | 8 |
| 3 | 24 | 6 |
| 4 | 28 | 4 |
| 5 | 30 | 2 |
| 6 | 30 | 0 |
| 7 | 28 | -2 |

### Exceptions
- Rare collections (stamps, coins)
- Misers accumulating wealth
- Addictive substances (initially)

## Consumer Equilibrium (Single Commodity)

A consumer is in equilibrium when they derive maximum satisfaction from their expenditure. For a single commodity, equilibrium is reached when:

**MU = Price (in terms of money)**

### Conditions
- If MU > Price: Consumer should buy more (gains surplus satisfaction)
- If MU < Price: Consumer should buy less (paying more than satisfaction received)
- If MU = Price: Consumer is in equilibrium (no incentive to change)

## Consumer Equilibrium (Two Commodities)

When a consumer purchases two goods (X and Y) with a limited budget, equilibrium is achieved when:

**MUx/Px = MUy/Py = MU of money (constant)**

This is known as the **Law of Equi-Marginal Utility** or Gossen's Second Law.

### Conditions for Equilibrium
1. MUx/Px = MUy/Py (proportionality condition)
2. MU of each good is diminishing (second-order condition)
3. Budget is fully spent: Px.Qx + Py.Qy = Income

### Explanation
If MUx/Px > MUy/Py, the consumer gets more satisfaction per rupee from X and should buy more of X and less of Y until equality is restored.
""",
    },
    {
        "chapter_number": 3,
        "slug": "consumers-equilibrium-indifference-curve",
        "title": "Consumer's Equilibrium using Indifference Curve",
        "description": "Analysis of consumer equilibrium using the ordinal utility approach including indifference curves, budget lines, and optimal consumer choice.",
        "topics": [
            "Indifference Curve",
            "Properties of Indifference Curves",
            "Budget Line",
            "Consumer Equilibrium using Indifference Curve",
            "Effects of Change in Income and Prices",
        ],
        "keywords": [
            "indifference curve", "budget line", "ordinal utility", "marginal rate of substitution",
            "consumer equilibrium", "IC analysis", "budget constraint", "income effect", "price effect",
        ],
        "body_markdown": """# Consumer's Equilibrium using Indifference Curve

## Indifference Curve

An indifference curve is a curve that shows all those combinations of two goods which give the consumer equal satisfaction. The consumer is indifferent between any two points on the same indifference curve.

### Indifference Schedule
| Combination | Good X | Good Y | Satisfaction |
|-------------|--------|--------|-------------|
| A | 1 | 12 | Same |
| B | 2 | 8 | Same |
| C | 3 | 5 | Same |
| D | 4 | 3 | Same |

### Marginal Rate of Substitution (MRS)
MRS is the rate at which a consumer is willing to substitute one good for another while maintaining the same level of satisfaction.

MRS_xy = Change in Y / Change in X = -dY/dX

MRS diminishes as we move along the indifference curve from left to right.

## Properties of Indifference Curves

1. **Downward sloping:** An IC slopes downward from left to right because to consume more of one good, the consumer must give up some of the other to maintain the same satisfaction level.

2. **Convex to the origin:** Due to diminishing MRS, the consumer gives up less and less of good Y for each additional unit of X.

3. **Higher IC represents higher satisfaction:** A higher indifference curve (further from origin) represents a greater level of satisfaction.

4. **Two ICs never intersect:** If they did, it would violate the transitivity and consistency assumptions of consumer preferences.

5. **IC need not be parallel:** The rate of substitution may differ at different levels of satisfaction.

## Budget Line

A budget line (also called budget constraint or price line) shows all combinations of two goods that a consumer can afford with a given income at given prices.

### Equation of Budget Line
Px.X + Py.Y = M

Where:
- Px = Price of good X
- Py = Price of good Y
- M = Consumer's income (money budget)
- X, Y = Quantities of goods X and Y

### Properties of Budget Line
- Slope = -Px/Py (ratio of prices)
- X-intercept = M/Px
- Y-intercept = M/Py
- Points on the line represent full expenditure of income
- Points below the line represent under-spending
- Points above the line are unaffordable

### Shifts in Budget Line
- **Parallel shift outward:** Increase in income (prices constant)
- **Parallel shift inward:** Decrease in income (prices constant)
- **Rotation:** Change in price of one good (income and other price constant)

## Consumer Equilibrium using Indifference Curve

The consumer reaches equilibrium at the point where the budget line is tangent to an indifference curve.

### Conditions for Equilibrium
1. **Necessary condition:** MRS_xy = Px/Py (slope of IC = slope of budget line)
2. **Sufficient condition:** IC must be convex to origin at the point of tangency

### Explanation
At the tangent point:
- The consumer is on the highest possible IC given the budget constraint
- No further reallocation of expenditure can increase satisfaction
- The rate at which the consumer is willing to substitute equals the rate at which the market allows substitution

## Effects of Change in Income and Prices

### Income Effect
When income changes (prices remaining constant), the budget line shifts parallelly. The locus of equilibrium points at different income levels is called the **Income Consumption Curve (ICC)**.
- For normal goods: As income rises, demand increases
- For inferior goods: As income rises, demand decreases

### Price Effect
When the price of one good changes (income and other price constant), the budget line rotates. The locus of equilibrium points is called the **Price Consumption Curve (PCC)**.

### Substitution Effect
The change in demand due solely to a change in relative prices (real income held constant) is called the substitution effect. It always works in the direction opposite to the price change.
""",
    },
    {
        "chapter_number": 4,
        "slug": "demand",
        "title": "Demand",
        "description": "Comprehensive study of demand including the law of demand, determinants, elasticity of demand, and movement along vs shift of the demand curve.",
        "topics": [
            "Meaning and Determinants of Demand",
            "Law of Demand",
            "Demand Schedule and Demand Curve",
            "Movement Along and Shift of Demand Curve",
            "Price Elasticity of Demand",
            "Factors Affecting Elasticity",
        ],
        "keywords": [
            "demand", "law of demand", "demand curve", "demand schedule",
            "elasticity of demand", "price elasticity", "determinants of demand",
            "shift in demand", "extension contraction",
        ],
        "body_markdown": """# Demand

## Meaning and Determinants of Demand

Demand refers to the quantity of a good that a consumer is willing and able to buy at various prices during a given period of time. Mere desire without purchasing power is not demand.

### Determinants of Demand
1. **Price of the good (Px):** Most important determinant; inversely related to demand
2. **Income of the consumer (Y):** Directly related for normal goods, inversely for inferior goods
3. **Prices of related goods:**
   - Substitutes (Ps): Directly related (e.g., tea and coffee)
   - Complements (Pc): Inversely related (e.g., car and petrol)
4. **Tastes and preferences (T):** Favourable tastes increase demand
5. **Expectations of future prices:** Expected price rise increases current demand
6. **Number of consumers:** More consumers means higher market demand

### Demand Function
Dx = f(Px, Y, Ps, Pc, T, E, N)

## Law of Demand

The Law of Demand states that, other things remaining constant (ceteris paribus), there is an inverse relationship between the price of a good and the quantity demanded.

"When the price of a good rises, its quantity demanded falls, and when price falls, quantity demanded rises."

### Assumptions (Ceteris Paribus)
- Income of the consumer remains constant
- Prices of related goods remain constant
- Tastes and preferences do not change
- No change in expectations about future prices
- No change in the number of consumers

### Reasons for the Law of Demand
1. **Income effect:** A fall in price increases real income, enabling more purchases
2. **Substitution effect:** A cheaper good is substituted for relatively expensive goods
3. **New buyers:** Lower prices attract new buyers
4. **Multiple uses:** At lower prices, the good is put to additional uses

### Exceptions to the Law of Demand
- Giffen goods (inferior goods where income effect outweighs substitution effect)
- Veblen goods (conspicuous consumption/status goods)
- Expectation of future price changes
- Necessities during emergencies

## Demand Schedule and Demand Curve

### Individual Demand Schedule
| Price (Rs) | Quantity Demanded |
|-----------|------------------|
| 10 | 1 |
| 8 | 2 |
| 6 | 3 |
| 4 | 4 |
| 2 | 5 |

### Market Demand
Market demand is the horizontal summation of all individual demand curves at each price level.

### Demand Curve
- Slopes downward from left to right (negative slope)
- Shows inverse relationship between price and quantity
- Plotted with price on Y-axis and quantity on X-axis

## Movement Along and Shift of Demand Curve

### Movement Along the Demand Curve (Change in Quantity Demanded)
- Caused by change in price of the good only
- **Extension:** Price falls, quantity demanded increases (downward movement)
- **Contraction:** Price rises, quantity demanded decreases (upward movement)

### Shift of the Demand Curve (Change in Demand)
- Caused by change in any factor other than price
- **Rightward shift (Increase in demand):** Increase in income, favourable taste change, rise in price of substitute
- **Leftward shift (Decrease in demand):** Decrease in income, unfavourable taste change, fall in price of substitute

## Price Elasticity of Demand

Price elasticity of demand measures the degree of responsiveness of quantity demanded to a change in price.

### Formula
Ed = Percentage change in quantity demanded / Percentage change in price
Ed = (Change in Q / Q) / (Change in P / P)

### Types of Elasticity
1. **Perfectly elastic (Ed = infinity):** Horizontal demand curve
2. **Highly elastic (Ed > 1):** Luxury goods
3. **Unitary elastic (Ed = 1):** Rectangular hyperbola demand curve
4. **Inelastic (Ed < 1):** Necessities
5. **Perfectly inelastic (Ed = 0):** Vertical demand curve

### Methods of Measuring Elasticity
1. **Percentage method:** Ed = (%Change in Qd) / (%Change in P)
2. **Total expenditure method:** Comparing total expenditure before and after price change
3. **Geometric/Point method:** Ed = Lower segment / Upper segment (on a linear demand curve)

## Factors Affecting Elasticity of Demand

1. **Nature of the good:** Necessities are inelastic; luxuries are elastic
2. **Availability of substitutes:** More substitutes means more elastic
3. **Proportion of income spent:** Higher proportion means more elastic
4. **Time period:** Demand is more elastic in the long run
5. **Multiple uses:** Goods with many uses have elastic demand
6. **Habit formation:** Habitual goods have inelastic demand
""",
    },
    # ===================================================================
    # Unit III: Producer Behaviour and Supply
    # ===================================================================
    {
        "chapter_number": 5,
        "slug": "production",
        "title": "Production",
        "description": "Study of production function, total product, average product, marginal product, law of variable proportions, and returns to scale.",
        "topics": [
            "Production Function",
            "Total Product, Average Product, Marginal Product",
            "Law of Variable Proportions",
            "Returns to Scale",
        ],
        "keywords": [
            "production function", "total product", "average product", "marginal product",
            "law of variable proportions", "returns to scale", "factors of production",
            "short run", "long run", "diminishing returns",
        ],
        "body_markdown": """# Production

## Production Function

A production function shows the technical relationship between physical inputs and physical output of a good. It specifies the maximum output that can be produced with a given set of inputs, given the existing technology.

### Short Run Production Function
In the short run, at least one factor of production is fixed. The production function is:
Q = f(L, K_bar) where L is variable (labour) and K_bar is fixed (capital).

### Long Run Production Function
In the long run, all factors are variable: Q = f(L, K)

### Assumptions
- State of technology is given and constant
- Factors of production are divisible
- Production is measured in physical units per unit of time
- Inputs are used efficiently

## Total Product, Average Product, Marginal Product

### Total Product (TP)
Total Product is the total quantity of output produced by all units of a variable factor employed with fixed factors during a given period.

### Average Product (AP)
Average Product is the output per unit of the variable factor employed.
AP = TP / L (where L is units of variable factor)

### Marginal Product (MP)
Marginal Product is the addition to total product when one more unit of the variable factor is employed.
MP_n = TP_n - TP_(n-1)

### Relationship between TP, AP, and MP
- When MP > AP, AP is rising
- When MP = AP, AP is at maximum
- When MP < AP, AP is falling
- When MP = 0, TP is at maximum
- When MP is negative, TP is falling

## Law of Variable Proportions (Law of Diminishing Returns)

This law states that when more and more units of a variable factor are combined with a fixed factor, initially total product increases at an increasing rate, then at a decreasing rate, and eventually begins to decline.

### Three Stages

**Stage I (Increasing Returns):**
- TP increases at an increasing rate
- MP rises and then starts falling
- AP rises throughout
- Reason: Better utilization of fixed factor; increasing efficiency of variable factor

**Stage II (Diminishing Returns):**
- TP increases at a diminishing rate
- Both AP and MP decline
- MP remains positive
- This is the rational stage of production
- Reason: Fixed factor becomes inadequate relative to variable factor

**Stage III (Negative Returns):**
- TP declines
- MP becomes negative
- AP continues to fall
- No rational producer will operate here
- Reason: Excessive use of variable factor hampers production

### Causes of Increasing Returns
- Indivisibility of fixed factors
- Increased efficiency due to specialization
- Better coordination between factors

### Causes of Diminishing Returns
- Overcrowding of variable factor
- Imperfect substitutability of factors
- Poor coordination due to excessive variable factor

## Returns to Scale

Returns to scale refers to the change in output when all factors of production are changed in the same proportion. It is a long-run concept.

### Types of Returns to Scale

1. **Increasing Returns to Scale:** Output increases by a larger proportion than the increase in inputs. Causes: Specialization, dimensional advantages, better technology.

2. **Constant Returns to Scale:** Output increases in the same proportion as the increase in inputs.

3. **Decreasing Returns to Scale:** Output increases by a smaller proportion than the increase in inputs. Causes: Managerial inefficiency, coordination difficulties.
""",
    },
    {
        "chapter_number": 6,
        "slug": "cost",
        "title": "Cost",
        "description": "Analysis of cost concepts including short-run and long-run costs, fixed and variable costs, and the shapes of cost curves.",
        "topics": [
            "Cost Function",
            "Short Run Costs",
            "Fixed Cost and Variable Cost",
            "Total Cost, Average Cost, Marginal Cost",
            "Relationship Between Cost Curves",
            "Long Run Costs",
        ],
        "keywords": [
            "cost function", "fixed cost", "variable cost", "total cost",
            "average cost", "marginal cost", "short run cost", "long run average cost",
            "U-shaped cost curve", "economies of scale",
        ],
        "body_markdown": """# Cost

## Cost Function

A cost function shows the relationship between the output produced and the cost of production. It is derived from the production function and input prices.

### Types of Cost
- **Explicit costs:** Actual monetary payments made to factors of production (rent, wages, materials)
- **Implicit costs:** Opportunity costs of self-owned resources (imputed rent, own labour)
- **Economic cost = Explicit cost + Implicit cost**
- **Accounting cost = Explicit cost only**

## Short Run Costs

In the short run, some factors are fixed and some are variable. This gives rise to fixed and variable costs.

## Fixed Cost and Variable Cost

### Total Fixed Cost (TFC)
- Costs that do not change with the level of output
- Must be paid even when output is zero
- Examples: Rent, insurance, depreciation, salaries of permanent staff
- TFC curve is a horizontal straight line

### Total Variable Cost (TVC)
- Costs that change with the level of output
- Zero when output is zero
- Examples: Raw materials, wages of casual labour, power, fuel
- TVC curve is inverse S-shaped

## Total Cost, Average Cost, Marginal Cost

### Total Cost (TC)
TC = TFC + TVC

### Average Fixed Cost (AFC)
AFC = TFC / Q
- AFC falls continuously as output increases
- AFC curve is a rectangular hyperbola

### Average Variable Cost (AVC)
AVC = TVC / Q
- AVC curve is U-shaped
- Initially falls due to increasing returns, then rises due to diminishing returns

### Average Total Cost (ATC or AC)
ATC = TC / Q = AFC + AVC
- ATC curve is U-shaped
- The vertical distance between ATC and AVC equals AFC

### Marginal Cost (MC)
MC = Change in TC / Change in Q = TC_n - TC_(n-1)
- MC is the cost of producing one additional unit of output
- MC curve is U-shaped
- MC is independent of TFC

## Relationship Between Cost Curves

### MC and ATC Relationship
- When MC < ATC: ATC is falling
- When MC = ATC: ATC is at minimum (MC cuts ATC at its lowest point)
- When MC > ATC: ATC is rising

### MC and AVC Relationship
- When MC < AVC: AVC is falling
- When MC = AVC: AVC is at minimum
- When MC > AVC: AVC is rising
- MC cuts AVC at its minimum point before cutting ATC at its minimum

### Why Cost Curves are U-shaped
- Due to the law of variable proportions
- Initially: Increasing returns cause costs to fall
- Later: Diminishing returns cause costs to rise

## Long Run Costs

### Long Run Average Cost (LRAC)
In the long run, all costs are variable. The LRAC curve is the envelope of all short-run average cost curves.

- LRAC is also U-shaped but flatter than SAC curves
- It is also called the "planning curve" or "envelope curve"

### Reasons for U-shape of LRAC
- **Falling portion:** Economies of scale (technical, managerial, financial, marketing)
- **Rising portion:** Diseconomies of scale (managerial inefficiency, coordination problems)

### Long Run Marginal Cost (LRMC)
- LRMC cuts LRAC at its minimum point
- When LRMC < LRAC: LRAC is falling
- When LRMC > LRAC: LRAC is rising
""",
    },
    {
        "chapter_number": 7,
        "slug": "revenue",
        "title": "Revenue",
        "description": "Study of revenue concepts including total revenue, average revenue, marginal revenue, and their relationships under different market structures.",
        "topics": [
            "Total Revenue",
            "Average Revenue",
            "Marginal Revenue",
            "Relationship Between AR and MR",
            "Revenue Curves Under Perfect Competition and Monopoly",
        ],
        "keywords": [
            "total revenue", "average revenue", "marginal revenue",
            "AR MR relationship", "revenue curves", "perfect competition",
            "monopoly revenue", "price taker",
        ],
        "body_markdown": """# Revenue

## Total Revenue (TR)

Total Revenue is the total amount of money received by a firm from the sale of a given quantity of output.

### Formula
TR = Price x Quantity = P x Q

### Under Perfect Competition
- TR increases at a constant rate (since price is constant)
- TR curve is a straight line through the origin with positive slope

### Under Imperfect Competition (Monopoly)
- TR first increases at a decreasing rate, reaches a maximum, then declines
- This is because to sell more, the firm must lower the price

## Average Revenue (AR)

Average Revenue is the revenue received per unit of output sold. It is also equal to the price of the product.

### Formula
AR = TR / Q = (P x Q) / Q = P

AR is always equal to price.

### AR Curve
- Under perfect competition: AR is a horizontal line (price is constant)
- Under monopoly: AR slopes downward (must reduce price to sell more)
- The AR curve is the same as the demand curve faced by the firm

## Marginal Revenue (MR)

Marginal Revenue is the addition to total revenue when one more unit of output is sold.

### Formula
MR_n = TR_n - TR_(n-1)

### Under Perfect Competition
- MR is constant and equal to AR (price)
- MR curve coincides with the AR curve (horizontal line)

### Under Monopoly
- MR falls faster than AR
- MR curve lies below the AR curve
- MR can become negative (when TR starts declining)

## Relationship Between AR and MR

### Under Perfect Competition
- AR = MR = Price (all are equal and constant)
- Both curves are the same horizontal straight line

### Under Monopoly (Straight-line Demand Curve)
- Both AR and MR are downward sloping
- MR falls twice as fast as AR
- When AR is falling, MR < AR

### Key Relationships
- When MR > 0, TR is rising
- When MR = 0, TR is at maximum
- When MR < 0, TR is falling

## Revenue Curves Under Perfect Competition and Monopoly

### Perfect Competition
- Many sellers, homogeneous product, price taker
- AR = MR = Price (horizontal line)
- TR is a straight line with positive slope from origin

### Monopoly
- Single seller, no close substitutes, price maker
- AR curve slopes downward (same as market demand curve)
- MR curve slopes downward and lies below AR
- TR curve rises, reaches maximum (when MR = 0), then falls
""",
    },
    {
        "chapter_number": 8,
        "slug": "profit-maximisation",
        "title": "Profit Maximisation",
        "description": "Understanding profit maximisation conditions for firms, including the MC=MR approach and the TR-TC approach under different market structures.",
        "topics": [
            "Meaning of Profit",
            "Conditions for Profit Maximisation",
            "TR-TC Approach",
            "MR-MC Approach",
            "Normal Profit and Supernormal Profit",
        ],
        "keywords": [
            "profit maximisation", "MC equals MR", "TR TC approach", "normal profit",
            "supernormal profit", "break even", "shut down point",
            "equilibrium of firm", "producer equilibrium",
        ],
        "body_markdown": """# Profit Maximisation

## Meaning of Profit

Profit is the excess of total revenue over total cost of production.

### Types of Profit
- **Normal Profit:** The minimum profit required to keep a firm in business. It is included in total cost as the opportunity cost of entrepreneurship.
- **Supernormal (Abnormal/Economic) Profit:** Profit earned over and above normal profit. Supernormal Profit = TR - TC (where TC includes normal profit).
- **Accounting Profit:** TR - Explicit costs
- **Economic Profit:** TR - (Explicit costs + Implicit costs)

## Conditions for Profit Maximisation

A firm maximises profit when:
1. **Necessary condition:** MC = MR (Marginal Cost equals Marginal Revenue)
2. **Sufficient condition:** MC curve cuts MR curve from below (MC must be rising at the point of intersection)

### Why MC = MR?
- If MR > MC: Producing one more unit adds more to revenue than to cost, so profit increases. The firm should expand output.
- If MC > MR: Producing one more unit adds more to cost than to revenue, so profit decreases. The firm should reduce output.
- If MC = MR: No further adjustment can increase profit. The firm is in equilibrium.

## TR-TC Approach

Under this approach, profit is maximized when the difference between Total Revenue and Total Cost is greatest.

### Steps
1. Calculate TR and TC at each output level
2. Find the output where (TR - TC) is maximum
3. At this point, the slope of TR equals the slope of TC (i.e., MR = MC)

## MR-MC Approach

This is the most commonly used approach for determining equilibrium output.

### Under Perfect Competition
- AR = MR = Price (constant)
- Equilibrium: MC = MR = Price
- MC curve must be rising at equilibrium
- The MC curve above the AVC curve is the supply curve of the firm

### Under Monopoly
- MR is less than AR (price)
- Equilibrium: MC = MR (where MC cuts MR from below)
- Price is read from the AR (demand) curve at equilibrium output
- Price > MC at equilibrium (monopoly power)

## Normal Profit and Supernormal Profit

### Normal Profit (Zero Economic Profit)
- Occurs when TR = TC (including implicit costs)
- AR = ATC at equilibrium
- Firm earns just enough to stay in business

### Supernormal Profit
- Occurs when TR > TC
- AR > ATC at equilibrium output
- Firm earns above normal returns

### Losses and Shut-down
- Occurs when TR < TC (AR < ATC)
- Firm should continue if AR > AVC (covers variable costs)
- **Shut-down point:** AR = AVC
- If AR < AVC, firm should shut down immediately
""",
    },
    {
        "chapter_number": 9,
        "slug": "supply",
        "title": "Supply",
        "description": "Study of supply including the law of supply, supply schedule, supply curve, determinants of supply, and elasticity of supply.",
        "topics": [
            "Meaning and Determinants of Supply",
            "Law of Supply",
            "Supply Schedule and Supply Curve",
            "Movement Along and Shift of Supply Curve",
            "Elasticity of Supply",
        ],
        "keywords": [
            "supply", "law of supply", "supply curve", "supply schedule",
            "elasticity of supply", "determinants of supply",
            "shift in supply", "supply function",
        ],
        "body_markdown": """# Supply

## Meaning and Determinants of Supply

Supply refers to the quantity of a commodity that a producer is willing and able to offer for sale at various prices during a given period of time.

### Determinants of Supply
1. **Price of the good (P):** Directly related to supply (law of supply)
2. **Prices of inputs/factors:** Higher input prices reduce supply
3. **Technology:** Improved technology increases supply
4. **Government policy:** Taxes reduce supply; subsidies increase supply
5. **Prices of related goods:** Higher price of substitute goods in production reduces supply
6. **Number of firms:** More firms means greater market supply
7. **Expected future prices:** Expected price rise may reduce current supply

### Supply Function
Sx = f(Px, Pi, T, G, Pr, N, E)

## Law of Supply

The Law of Supply states that, other things remaining constant, there is a direct (positive) relationship between the price of a good and the quantity supplied.

"When the price of a good rises, its quantity supplied increases, and when price falls, quantity supplied decreases."

### Explanation
- Higher prices mean higher profits, motivating producers to supply more
- Higher prices make it worthwhile to incur higher marginal costs
- Supply curve slopes upward from left to right (positive slope)

### Exceptions to the Law of Supply
- Supply of perishable goods
- Agricultural goods (in very short period)
- Artistic and antique goods
- Labour supply (backward bending supply curve at high wages)

## Supply Schedule and Supply Curve

### Individual Supply Schedule
| Price (Rs) | Quantity Supplied |
|-----------|-----------------|
| 2 | 10 |
| 4 | 20 |
| 6 | 30 |
| 8 | 40 |
| 10 | 50 |

### Market Supply
Market supply is the horizontal summation of individual supply curves of all firms at each price level.

### Supply Curve
- Slopes upward from left to right (positive slope)
- Shows direct relationship between price and quantity supplied

## Movement Along and Shift of Supply Curve

### Movement Along the Supply Curve (Change in Quantity Supplied)
- Caused only by a change in the price of the good itself
- **Extension of supply:** Price rises, quantity supplied increases
- **Contraction of supply:** Price falls, quantity supplied decreases

### Shift of Supply Curve (Change in Supply)
- Caused by changes in factors other than price
- **Rightward shift (Increase in supply):** Fall in input prices, improvement in technology, government subsidy
- **Leftward shift (Decrease in supply):** Rise in input prices, imposition of tax, unfavourable weather

## Elasticity of Supply

Elasticity of supply measures the degree of responsiveness of quantity supplied to a change in price.

### Formula
Es = Percentage change in quantity supplied / Percentage change in price

### Types of Elasticity of Supply
1. **Perfectly elastic (Es = infinity):** Horizontal supply curve
2. **Highly elastic (Es > 1):** Supply curve flatter, passes through Y-axis
3. **Unitary elastic (Es = 1):** Supply curve passes through the origin
4. **Inelastic (Es < 1):** Supply curve steeper, passes through X-axis
5. **Perfectly inelastic (Es = 0):** Vertical supply curve

### Factors Affecting Elasticity of Supply
1. **Time period:** Longer time allows more adjustment (more elastic)
2. **Nature of the good:** Perishable goods have inelastic supply
3. **Cost of production:** If cost rises sharply, supply is inelastic
4. **Availability of resources:** Abundant resources make supply elastic
5. **Storage facilities:** Better storage means more elastic supply
""",
    },
    # ===================================================================
    # Unit IV: Market Equilibrium
    # ===================================================================
    {
        "chapter_number": 10,
        "slug": "market-equilibrium",
        "title": "Market Equilibrium",
        "description": "Understanding market equilibrium through the interaction of demand and supply, including equilibrium price determination and effects of shifts.",
        "topics": [
            "Meaning of Market Equilibrium",
            "Determination of Equilibrium Price",
            "Excess Demand and Excess Supply",
            "Effects of Shift in Demand and Supply",
            "Simultaneous Shifts",
        ],
        "keywords": [
            "market equilibrium", "equilibrium price", "equilibrium quantity",
            "excess demand", "excess supply", "demand supply interaction",
            "price determination", "market clearing",
        ],
        "body_markdown": """# Market Equilibrium

## Meaning of Market Equilibrium

Market equilibrium is a situation where the quantity demanded of a good equals the quantity supplied at a particular price. At this point, there is no tendency for the price to change.

### Equilibrium Price
The price at which quantity demanded equals quantity supplied is called the equilibrium price or market-clearing price.

### Equilibrium Quantity
The quantity bought and sold at the equilibrium price is called the equilibrium quantity.

## Determination of Equilibrium Price

Equilibrium is determined by the intersection of the demand curve and the supply curve.

### Using Schedules
| Price (Rs) | Qd | Qs | Situation |
|-----------|----|----|-----------|
| 10 | 100 | 20 | Excess demand |
| 20 | 80 | 40 | Excess demand |
| 30 | 60 | 60 | Equilibrium |
| 40 | 40 | 80 | Excess supply |
| 50 | 20 | 100 | Excess supply |

At Rs. 30: Qd = Qs = 60 units (Equilibrium)

### Algebraic Method
If demand function: Qd = a - bP and supply function: Qs = c + dP
At equilibrium: Qd = Qs, therefore a - bP = c + dP
Equilibrium price: P* = (a - c) / (b + d)

## Excess Demand and Excess Supply

### Excess Demand (Shortage)
- Occurs when Qd > Qs at a given price
- Price is below equilibrium
- Competition among buyers pushes price up
- As price rises: Qd falls and Qs rises until equilibrium is restored

### Excess Supply (Surplus)
- Occurs when Qs > Qd at a given price
- Price is above equilibrium
- Competition among sellers pushes price down
- As price falls: Qd rises and Qs falls until equilibrium is restored

### Self-correcting Mechanism
The market automatically moves toward equilibrium through the price mechanism.

## Effects of Shift in Demand and Supply

### Increase in Demand (Rightward shift)
- Equilibrium price increases
- Equilibrium quantity increases

### Decrease in Demand (Leftward shift)
- Equilibrium price decreases
- Equilibrium quantity decreases

### Increase in Supply (Rightward shift)
- Equilibrium price decreases
- Equilibrium quantity increases

### Decrease in Supply (Leftward shift)
- Equilibrium price increases
- Equilibrium quantity decreases

## Simultaneous Shifts

### Both Demand and Supply Increase
- Equilibrium quantity definitely increases
- Effect on price depends on relative magnitudes

### Demand Increases, Supply Decreases
- Equilibrium price definitely increases
- Effect on quantity depends on relative magnitudes

### Demand Decreases, Supply Increases
- Equilibrium price definitely decreases
- Effect on quantity depends on relative magnitudes

### Both Decrease
- Equilibrium quantity definitely decreases
- Effect on price depends on relative magnitudes
""",
    },
    {
        "chapter_number": 11,
        "slug": "applications-of-demand-and-supply",
        "title": "Applications of Demand and Supply",
        "description": "Practical applications of demand and supply analysis including price ceiling, price floor, and government intervention in markets.",
        "topics": [
            "Price Ceiling (Maximum Price)",
            "Price Floor (Minimum Price)",
            "Impact of Taxes on Equilibrium",
            "Impact of Subsidies on Equilibrium",
        ],
        "keywords": [
            "price ceiling", "price floor", "maximum price", "minimum price",
            "government intervention", "tax incidence", "subsidy",
            "market intervention", "rationing",
        ],
        "body_markdown": """# Applications of Demand and Supply

## Price Ceiling (Maximum Price)

A price ceiling is the maximum price fixed by the government below the equilibrium price. It is imposed to protect the interests of consumers.

### Characteristics
- Set below the equilibrium price (otherwise it is ineffective)
- Creates excess demand (shortage) in the market
- Leads to rationing and black markets
- Examples: Rent control, essential commodities during emergencies

### Effects of Price Ceiling
1. **Shortage:** Quantity demanded exceeds quantity supplied
2. **Rationing:** Government introduces rationing to distribute limited supply fairly
3. **Black market:** Some sellers sell at prices above the ceiling illegally
4. **Deterioration of quality:** Producers may reduce quality to cut costs
5. **Reduced supply:** Producers have less incentive to produce at lower price

## Price Floor (Minimum Price)

A price floor is the minimum price fixed by the government above the equilibrium price. It is imposed to protect the interests of producers/sellers.

### Characteristics
- Set above the equilibrium price (otherwise it is ineffective)
- Creates excess supply (surplus) in the market
- Government may need to buy the surplus
- Examples: Minimum Support Price (MSP) for agricultural products, Minimum wages

### Effects of Price Floor
1. **Surplus:** Quantity supplied exceeds quantity demanded
2. **Buffer stock:** Government purchases excess supply
3. **Higher consumer prices:** Consumers pay more than equilibrium price
4. **Increased production:** Producers have incentive to produce more at higher price

## Impact of Taxes on Equilibrium

When the government imposes a tax on a commodity:
- Supply curve shifts leftward (upward) by the amount of tax
- New equilibrium has higher price and lower quantity
- The tax burden is shared between buyers and sellers

### Tax Incidence
- **Burden on consumers:** Rise in price = New price - Old price
- **Burden on producers:** Tax per unit - Rise in price
- The more inelastic side bears a greater share of the tax

### Effects
- Equilibrium price rises (but by less than the full tax)
- Equilibrium quantity falls
- Government earns tax revenue = Tax per unit x New quantity

## Impact of Subsidies on Equilibrium

When the government provides a subsidy to producers:
- Supply curve shifts rightward (downward) by the amount of subsidy
- New equilibrium has lower price and higher quantity
- The benefit is shared between buyers and sellers

### Effects of Subsidy
- Equilibrium price falls
- Equilibrium quantity increases
- Consumers benefit from lower prices
- Producers benefit from higher effective price received
- Government expenditure = Subsidy per unit x New quantity
""",
    },
    # ===================================================================
    # Part B: Statistics for Economics
    # Unit I: Introduction
    # ===================================================================
    {
        "chapter_number": 12,
        "slug": "statistics-in-economics",
        "title": "Statistics in Economics",
        "description": "Introduction to statistics and its role in economics, covering meaning, scope, importance, and limitations of statistics.",
        "topics": [
            "Meaning and Definition of Statistics",
            "Scope and Importance of Statistics in Economics",
            "Limitations of Statistics",
            "Statistical Data: Primary and Secondary",
        ],
        "keywords": [
            "statistics", "economics statistics", "quantitative data",
            "primary data", "secondary data", "statistical methods",
            "descriptive statistics", "inferential statistics",
        ],
        "body_markdown": """# Statistics in Economics

## Meaning and Definition of Statistics

Statistics has two meanings: (1) as numerical data (plural sense) and (2) as a discipline/methodology (singular sense).

### Definitions

**As Data (Plural Sense):**
Statistics are numerical statements of facts in any department of enquiry, placed in relation to each other. - A.L. Bowley

**As a Discipline (Singular Sense):**
Statistics is the science of collecting, organizing, presenting, analyzing, and interpreting numerical data to make decisions in the face of uncertainty. - Croxton and Cowden

### Characteristics of Statistics (as data)
1. Aggregate of facts (not single observations)
2. Numerically expressed (quantitative)
3. Collected in a systematic manner
4. Collected for a predetermined purpose
5. Placed in relation to each other (comparable)
6. Affected by multiple causes
7. Reasonable degree of accuracy

## Scope and Importance of Statistics in Economics

### Scope
- **Consumption:** Consumer surveys, demand analysis, cost of living indices
- **Production:** Production planning, quality control, productivity measurement
- **Exchange:** Market analysis, price determination, trade statistics
- **Distribution:** Income distribution, wage determination, poverty measurement
- **Public Finance:** Budget analysis, tax collection, public expenditure

### Importance
1. Gives precision to economic statements
2. Helps government in planning and policy making
3. Helps in testing and verifying economic laws
4. Enables forecasting of economic trends
5. Facilitates comparison across time periods and regions
6. Helps businesses in market research and planning
7. Essential for measuring economic growth and development

## Limitations of Statistics

1. Only deals with numerical data (cannot study qualitative aspects)
2. Deals with aggregates only (not individual measurements)
3. Results are approximations, not exact measures
4. Can be misused if data is manipulated
5. Homogeneous data required for comparison
6. Requires trained personnel for proper use
7. Must be used along with other methods of analysis

## Statistical Data: Primary and Secondary

### Primary Data
Data collected by the investigator for the first time for a specific purpose.
- **Methods:** Direct personal investigation, indirect oral investigation, mailed questionnaire, schedule method, telephone interview
- **Advantages:** Reliable, accurate, suitable for purpose
- **Disadvantages:** Time-consuming, expensive, limited coverage

### Secondary Data
Data already collected by someone else and available for use.
- **Sources:** Government reports, journals, books, organizational records
- **Advantages:** Saves time and money, wider coverage
- **Disadvantages:** May not be suitable, accuracy not guaranteed

### Difference Between Primary and Secondary Data
| Basis | Primary Data | Secondary Data |
|-------|-------------|---------------|
| Collection | Original/first hand | Already available |
| Cost | Expensive | Economical |
| Time | Time-consuming | Quick |
| Suitability | Highly suitable | May not fit |
| Accuracy | More accurate | Less reliable |
""",
    },
    # ===================================================================
    # Unit II: Collection, Organisation and Presentation of Data
    # ===================================================================
    {
        "chapter_number": 13,
        "slug": "collection-of-data",
        "title": "Collection of Data",
        "description": "Methods of data collection including census and sampling methods, questionnaire design, and sources of data.",
        "topics": [
            "Census Method",
            "Sampling Methods",
            "Random and Non-Random Sampling",
            "Methods of Collecting Primary Data",
            "Sources of Secondary Data",
            "Census of India and NSSO",
        ],
        "keywords": [
            "data collection", "census method", "sampling method", "random sampling",
            "questionnaire", "primary data", "secondary data", "NSSO",
            "census of India", "survey",
        ],
        "body_markdown": """# Collection of Data

## Census Method (Complete Enumeration)

In the census method, data is collected from each and every unit of the population (universe).

### Features
- Covers entire population
- Provides accurate and reliable data
- No sampling error
- Suitable when population is small
- Used in Census of India (every 10 years)

### Advantages
- Complete information about population
- High accuracy
- Data can be used for detailed sub-group analysis

### Disadvantages
- Very expensive and time-consuming
- Requires large workforce
- Not feasible for large or infinite populations
- Data may become outdated by the time it is compiled

## Sampling Methods

In sampling, data is collected from a representative portion (sample) of the population, and conclusions are drawn about the entire population.

### Advantages of Sampling over Census
- Less expensive and less time-consuming
- Greater accuracy (fewer non-sampling errors)
- Feasible for large populations
- Suitable for destructive testing

## Random and Non-Random Sampling

### Random (Probability) Sampling
Every unit in the population has an equal and known chance of being selected.

**Types:**
1. **Simple Random Sampling:** Each unit has equal probability (lottery method, random number tables)
2. **Systematic Sampling:** Every kth item is selected (k = N/n)
3. **Stratified Sampling:** Population divided into strata, samples drawn from each
4. **Cluster Sampling:** Entire clusters randomly selected

### Non-Random (Non-Probability) Sampling
Selection based on judgment or convenience, not probability.

**Types:**
1. **Judgment Sampling:** Based on expert judgment
2. **Convenience Sampling:** Most accessible units selected
3. **Quota Sampling:** Sample matches population proportions

## Methods of Collecting Primary Data

### 1. Direct Personal Investigation
Investigator personally collects data. Suitable for intensive studies with small population.

### 2. Indirect Oral Investigation
Data collected from third parties who have knowledge about the subject.

### 3. Mailed Questionnaire Method
Questionnaires sent by post/email. Wide coverage but low response rate.

### 4. Schedule Method (Enumerator Method)
Trained enumerators visit respondents. High response rate, suitable for illiterate respondents.

### 5. Telephone Interview
Quick and relatively inexpensive but limited to phone owners.

## Sources of Secondary Data

### Published Sources
- Government publications (Census reports, RBI Bulletin, Economic Survey)
- International organizations (UN, World Bank, IMF)
- Journals, newspapers, and research publications

### Unpublished Sources
- Records of government departments
- Studies by research scholars
- Records of private organizations

## Census of India and NSSO

### Census of India
- Conducted every 10 years (decennial)
- First census: 1872 (first complete: 1881)
- Provides population data for planning and policy

### National Sample Survey Organisation (NSSO)
- Established in 1950
- Conducts nationwide sample surveys
- Covers employment, consumer expenditure, health, education
- Now part of National Statistical Office (NSO)
""",
    },
    {
        "chapter_number": 14,
        "slug": "organisation-of-data",
        "title": "Organisation of Data",
        "description": "Methods of organizing raw data into meaningful forms including classification, tabulation, and frequency distribution.",
        "topics": [
            "Classification of Data",
            "Variables: Discrete and Continuous",
            "Frequency Distribution",
            "Class Intervals and Class Limits",
            "Cumulative Frequency Distribution",
        ],
        "keywords": [
            "data organisation", "classification", "frequency distribution",
            "class interval", "cumulative frequency", "discrete variable",
            "continuous variable", "tabulation", "tally marks",
        ],
        "body_markdown": """# Organisation of Data

## Classification of Data

Classification is the process of arranging data into groups or classes according to common characteristics. It transforms raw data into an organized form.

### Objectives of Classification
- To condense the mass of data
- To make data comparable
- To highlight significant features
- To enable statistical analysis

### Basis of Classification
1. **Geographical:** By region, state, country
2. **Chronological/Temporal:** By time period
3. **Qualitative:** By attributes or qualities (e.g., gender, literacy)
4. **Quantitative:** By numerical values (e.g., income groups, age groups)

## Variables: Discrete and Continuous

### Discrete Variable
A variable that can take only specific, distinct values (usually whole numbers). There are gaps between possible values.
- Examples: Number of students, number of children, number of accidents
- Can be counted

### Continuous Variable
A variable that can take any value within a given range. There are no gaps between possible values.
- Examples: Height, weight, temperature, time, income
- Can be measured and take fractional values

## Frequency Distribution

A frequency distribution is a tabular arrangement of data showing the number of observations (frequency) in each class or category.

### Steps to Create
1. Determine the range (Maximum value - Minimum value)
2. Decide the number of classes
3. Determine the class width (Range / Number of classes)
4. Set up class intervals
5. Tally the observations into appropriate classes
6. Count frequencies

### Types
1. **Discrete (Ungrouped) Frequency Distribution:** Individual values with frequencies
2. **Continuous (Grouped) Frequency Distribution:** Data in class intervals

### Example
| Class Interval | Frequency |
|---------------|-----------|
| 0-10 | 4 |
| 10-20 | 8 |
| 20-30 | 12 |
| 30-40 | 10 |
| 40-50 | 6 |
| Total | 40 |

## Class Intervals and Class Limits

### Types of Class Intervals
1. **Exclusive method (continuous):** Upper limit of one class = Lower limit of next class (e.g., 0-10, 10-20)
2. **Inclusive method:** Both limits included (e.g., 0-9, 10-19, 20-29)

### Class Limits and Class Boundaries
- **Class limits:** The stated end values of a class interval
- **Class boundaries (True limits):** Used for continuous data
  - Lower boundary = LCL - 0.5 (for inclusive method)
  - Upper boundary = UCL + 0.5 (for inclusive method)

### Class Mark (Mid-point)
Class mark = (Lower limit + Upper limit) / 2

## Cumulative Frequency Distribution

### Less-than Cumulative Frequency
Running total of frequencies up to the upper boundary of each class.

### More-than Cumulative Frequency
Running total of frequencies from the lower boundary of each class downward.

### Example
| Class | Frequency | Less-than CF | More-than CF |
|-------|-----------|-------------|-------------|
| 0-10 | 4 | 4 | 40 |
| 10-20 | 8 | 12 | 36 |
| 20-30 | 12 | 24 | 28 |
| 30-40 | 10 | 34 | 16 |
| 40-50 | 6 | 40 | 6 |

### Ogive (Cumulative Frequency Curve)
- Less-than ogive: Rising curve plotted using upper boundaries
- More-than ogive: Falling curve plotted using lower boundaries
- Intersection of two ogives gives the median
""",
    },
    {
        "chapter_number": 15,
        "slug": "presentation-of-data",
        "title": "Presentation of Data",
        "description": "Methods of presenting statistical data using tables, diagrams, and graphs including bar diagrams, pie charts, histograms, and frequency polygons.",
        "topics": [
            "Tabular Presentation",
            "Diagrammatic Presentation",
            "Bar Diagrams",
            "Pie Charts",
            "Histograms",
            "Frequency Polygon and Ogive",
        ],
        "keywords": [
            "data presentation", "tabulation", "bar diagram", "pie chart",
            "histogram", "frequency polygon", "ogive", "line graph",
            "diagrammatic representation",
        ],
        "body_markdown": """# Presentation of Data

## Tabular Presentation

A table is a systematic arrangement of data in rows and columns. It is the most fundamental method of presenting data.

### Parts of a Table
1. **Title:** Brief description of the table contents
2. **Table number:** For reference
3. **Column headings (Caption):** Describes each column
4. **Row headings (Stub):** Describes each row
5. **Body:** The actual data
6. **Source note:** Origin of the data
7. **Footnotes:** Additional explanations

### Advantages of Tabulation
- Simplifies complex data
- Facilitates comparison
- Reveals patterns and trends
- Saves space
- Facilitates further statistical analysis

## Diagrammatic Presentation

Diagrams are visual representations of data that make it easy to understand and compare at a glance.

### Advantages
- Attractive and eye-catching
- Easy to understand even for laypeople
- Facilitates quick comparison
- Creates lasting impression

### Limitations
- Cannot show exact values
- Can be misleading if not drawn properly
- Not suitable for further analysis

## Bar Diagrams

Bar diagrams use rectangular bars of equal width to represent data. The height of bars is proportional to the values.

### Types
1. **Simple Bar Diagram:** One bar for each category
2. **Multiple (Grouped) Bar Diagram:** Two or more bars for each category
3. **Component (Sub-divided) Bar Diagram:** Each bar divided into segments
4. **Percentage Bar Diagram:** All bars same height (100%), segments show proportions

### Rules
- Bars should have equal width
- Gap between bars should be uniform
- Scale should start from zero

## Pie Charts (Circular Diagrams)

A pie chart is a circle divided into sectors whose areas are proportional to the values they represent.

### Steps to Draw
1. Calculate percentage of each component
2. Convert to degrees: Angle = (Component value / Total) x 360
3. Draw circle and mark sectors using a protractor
4. Label each sector

### Advantages
- Shows proportions clearly
- Visually appealing
- Easy to understand composition

## Histograms

A histogram is a graphical representation of a frequency distribution using adjacent rectangles.

### Characteristics
- X-axis shows class intervals (continuous)
- Y-axis shows frequency (or frequency density)
- Bars are adjacent (no gaps) since data is continuous
- Area of each bar is proportional to the frequency
- For unequal class intervals: use frequency density = frequency / class width

### Difference from Bar Diagram
| Histogram | Bar Diagram |
|-----------|-------------|
| No gaps between bars | Gaps between bars |
| Area represents frequency | Height represents value |
| For continuous data | For discrete/categorical data |

## Frequency Polygon and Ogive

### Frequency Polygon
A line graph joining the mid-points of the tops of histogram bars.

**Steps:**
1. Calculate class marks (mid-points)
2. Plot mid-points against frequencies
3. Join the points with straight lines
4. Close the polygon at both ends with zero frequency

### Ogive (Cumulative Frequency Curve)

**Types:**
1. **Less-than ogive:** Plot upper boundaries vs. less-than cumulative frequency (rising)
2. **More-than ogive:** Plot lower boundaries vs. more-than cumulative frequency (falling)

**Uses:**
- Finding median (at N/2 on Y-axis)
- Finding quartiles, deciles, percentiles
- Comparing distributions
""",
    },
    # ===================================================================
    # Unit III: Statistical Tools and Interpretation
    # ===================================================================
    {
        "chapter_number": 16,
        "slug": "measures-of-central-tendency",
        "title": "Measures of Central Tendency",
        "description": "Study of averages including arithmetic mean, median, and mode with their calculation methods, properties, and applications.",
        "topics": [
            "Meaning of Central Tendency",
            "Arithmetic Mean",
            "Median",
            "Mode",
            "Relationship Between Mean, Median, and Mode",
        ],
        "keywords": [
            "central tendency", "arithmetic mean", "median", "mode",
            "average", "weighted mean", "grouped data", "ungrouped data",
            "empirical relationship",
        ],
        "body_markdown": """# Measures of Central Tendency

## Meaning of Central Tendency

A measure of central tendency is a single value that represents an entire data set by identifying the central position. It indicates where most values in a distribution tend to cluster.

### Desirable Properties of a Good Average
1. Rigidly defined (clear formula)
2. Based on all observations
3. Easy to understand and calculate
4. Least affected by sampling fluctuations
5. Capable of further algebraic treatment
6. Not unduly affected by extreme values

## Arithmetic Mean

The arithmetic mean is the sum of all values divided by the number of values. It is the most commonly used average.

### For Ungrouped Data
Mean = Sum of all values / Number of values = Sigma(Xi) / N

### For Grouped Data (Discrete Series)
Mean = Sigma(fi.xi) / Sigma(fi)

### For Continuous Series
Mean = Sigma(fi.mi) / Sigma(fi) where mi = mid-point of class

### Short-cut Method
Mean = A + Sigma(fi.di) / Sigma(fi) where A = assumed mean, di = (xi - A)

### Step-deviation Method
Mean = A + [Sigma(fi.ui) / Sigma(fi)] x h where ui = (xi - A)/h, h = class width

### Properties
1. Sum of deviations from mean is always zero
2. Sum of squared deviations from mean is minimum
3. Combined mean of two groups can be calculated

### Merits
- Rigidly defined, based on all observations
- Easy to calculate
- Suitable for further algebraic treatment

### Demerits
- Affected by extreme values
- Cannot be determined graphically
- Meaningless for open-ended distributions without assumption

## Median

Median is the middle value of an ordered data set. It divides the distribution into two equal halves.

### For Ungrouped Data
1. Arrange data in ascending order
2. If N is odd: Median = (N+1)/2 th value
3. If N is even: Median = Average of N/2 th and (N/2 + 1)th values

### For Grouped Data (Continuous Series)
Median = L + [(N/2 - CF) / f] x h

Where: L = Lower boundary of median class, N = Total frequency,
CF = Cumulative frequency before median class, f = Frequency of median class,
h = Width of median class

### Merits
- Not affected by extreme values
- Can be determined graphically (using ogives)
- Suitable for open-ended distributions

### Demerits
- Not based on all observations
- Not suitable for further algebraic treatment

## Mode

Mode is the value that occurs most frequently in a data set.

### For Ungrouped Data
Mode = Value with highest frequency

### For Grouped Data (Continuous Series)
Mode = L + [(f1 - f0) / (2f1 - f0 - f2)] x h

Where: L = Lower boundary of modal class, f1 = Frequency of modal class,
f0 = Frequency of preceding class, f2 = Frequency of succeeding class,
h = Width of modal class

### Types
- **Unimodal:** One mode
- **Bimodal:** Two modes
- **Multimodal:** More than two modes

### Merits
- Easy to understand and calculate
- Not affected by extreme values
- Can be determined graphically

### Demerits
- Not rigidly defined (may not exist or may have multiple modes)
- Not suitable for further algebraic treatment

## Relationship Between Mean, Median, and Mode

### For Symmetrical Distribution
Mean = Median = Mode

### For Moderately Asymmetrical Distribution (Empirical Relationship)
Mode = 3 Median - 2 Mean

### Positively Skewed Distribution
Mean > Median > Mode

### Negatively Skewed Distribution
Mean < Median < Mode
""",
    },
    {
        "chapter_number": 17,
        "slug": "correlation",
        "title": "Correlation",
        "description": "Study of correlation analysis including types of correlation, methods of measuring correlation, and Karl Pearson's coefficient of correlation.",
        "topics": [
            "Meaning and Types of Correlation",
            "Methods of Studying Correlation",
            "Scatter Diagram",
            "Karl Pearson Coefficient of Correlation",
            "Spearman Rank Correlation",
        ],
        "keywords": [
            "correlation", "positive correlation", "negative correlation",
            "Karl Pearson", "scatter diagram", "coefficient of correlation",
            "rank correlation", "Spearman", "linear correlation",
        ],
        "body_markdown": """# Correlation

## Meaning and Types of Correlation

Correlation is a statistical technique that measures the degree and direction of relationship between two or more variables.

### Types Based on Direction
1. **Positive Correlation:** Both variables move in the same direction (e.g., height and weight)
2. **Negative Correlation:** Variables move in opposite directions (e.g., price and demand)
3. **Zero Correlation:** No relationship between variables

### Types Based on Number of Variables
1. **Simple Correlation:** Between two variables only
2. **Multiple Correlation:** Among three or more variables
3. **Partial Correlation:** Between two variables keeping others constant

### Types Based on Nature
1. **Linear Correlation:** Relationship plots as a straight line
2. **Non-linear (Curvilinear) Correlation:** Relationship plots as a curve

### Degree of Correlation
- Perfect: r = +1 or -1
- High: 0.75 to 1 (or -0.75 to -1)
- Moderate: 0.25 to 0.75 (or -0.25 to -0.75)
- Low: 0 to 0.25 (or 0 to -0.25)
- Zero: r = 0

## Methods of Studying Correlation

1. **Scatter Diagram:** Visual/graphical method
2. **Karl Pearson's Coefficient:** Mathematical method for quantitative data
3. **Spearman's Rank Correlation:** For ranked/ordinal data

## Scatter Diagram

A scatter diagram is a graphical representation where each pair of values (X, Y) is plotted as a point.

### Interpretation
- Points cluster upward left to right: Positive correlation
- Points cluster downward left to right: Negative correlation
- Points scattered randomly: No correlation
- Points on a straight line: Perfect correlation

### Advantages
- Simple and easy to understand
- Not affected by extreme values
- Gives a visual picture of relationship

### Limitations
- Does not give exact numerical value
- Subjective interpretation

## Karl Pearson Coefficient of Correlation

Karl Pearson's coefficient (r) measures linear correlation between two variables.

### Formula
r = Sigma[(Xi - X_bar)(Yi - Y_bar)] / [sqrt(Sigma(Xi - X_bar)^2) x sqrt(Sigma(Yi - Y_bar)^2)]

### Short-cut Formula
r = [N.Sigma(XY) - (Sigma X)(Sigma Y)] / [sqrt{N.Sigma X^2 - (Sigma X)^2} x sqrt{N.Sigma Y^2 - (Sigma Y)^2}]

### Properties
1. r lies between -1 and +1
2. r is a pure number (no units)
3. r is symmetric: r(X,Y) = r(Y,X)
4. r is not affected by change of origin and scale
5. r measures only linear relationship

### Interpretation
- r = +1: Perfect positive correlation
- r = -1: Perfect negative correlation
- r = 0: No linear correlation

### Merits
- Gives exact numerical value
- Gives direction and degree of relationship
- Most widely used and mathematically rigorous

### Limitations
- Assumes linear relationship
- Affected by extreme values
- Does not imply causation

## Spearman Rank Correlation

Used when data is in ordinal form (ranks) or when assumptions of Pearson's r are not met.

### Formula
rs = 1 - [6 x Sigma(D^2)] / [N(N^2 - 1)]

Where: D = Difference between ranks (R1 - R2), N = Number of pairs

### Steps
1. Rank the X values (R1) and Y values (R2) separately
2. Calculate D = R1 - R2 for each pair
3. Calculate D^2 and sum all D^2 values
4. Apply the formula

### When Ranks are Repeated
Assign average rank to tied values. Correction factor = (m^3 - m)/12 for each tie.

### Properties
- rs lies between -1 and +1
- Useful for qualitative data that can be ranked
- Less affected by extreme values
- Easier to compute for small samples
""",
    },
    {
        "chapter_number": 18,
        "slug": "index-numbers",
        "title": "Index Numbers",
        "description": "Study of index numbers including meaning, types, methods of construction, and applications in measuring price and quantity changes.",
        "topics": [
            "Meaning and Features of Index Numbers",
            "Types of Index Numbers",
            "Methods of Constructing Index Numbers",
            "Consumer Price Index",
            "Uses and Limitations of Index Numbers",
        ],
        "keywords": [
            "index numbers", "price index", "quantity index", "consumer price index",
            "base year", "Laspeyres", "Paasche", "Fisher", "weighted index",
            "wholesale price index", "cost of living index",
        ],
        "body_markdown": """# Index Numbers

## Meaning and Features of Index Numbers

An index number is a statistical measure designed to show changes in a variable or group of related variables over time, geographical location, or other characteristics.

### Definition
"An index number is a statistical device for measuring changes in the magnitude of a group of related variables." - Croxton and Cowden

### Features
1. Expressed as percentages (ratio multiplied by 100)
2. Relative measures (compare two situations)
3. Specialized averages (averages of relatives)
4. Measure changes that are not directly measurable
5. Based on samples (representative items selected)

### Important Terms
- **Base period:** Reference period (index = 100)
- **Current period:** Period for which index is calculated
- **Base year prices (P0):** Prices in base year
- **Current year prices (P1):** Prices in current year

## Types of Index Numbers

1. **Price Index Numbers:** Measure changes in price levels (WPI, CPI)
2. **Quantity Index Numbers:** Measure changes in volume of goods
3. **Value Index Numbers:** Measure changes in total value (price x quantity)
4. **Special Purpose:** Industrial production, stock market indices

## Methods of Constructing Index Numbers

### A. Unweighted Index Numbers

**1. Simple Aggregative Method:**
P01 = (Sigma P1 / Sigma P0) x 100

**2. Simple Average of Price Relatives:**
P01 = (Sigma (P1/P0 x 100)) / N

### B. Weighted Index Numbers

**1. Laspeyres Index (Base Year Weighted):**
P01 = (Sigma P1.Q0 / Sigma P0.Q0) x 100
- Uses base year quantities as weights
- Tends to overestimate (upward bias)

**2. Paasche Index (Current Year Weighted):**
P01 = (Sigma P1.Q1 / Sigma P0.Q1) x 100
- Uses current year quantities as weights
- Tends to underestimate (downward bias)

**3. Fisher's Ideal Index:**
P01 = sqrt(Laspeyres Index x Paasche Index)
- Geometric mean of Laspeyres and Paasche
- Called "ideal" because: free from bias, satisfies time reversal test, satisfies factor reversal test

### Tests of Adequacy
1. **Time Reversal Test:** P01 x P10 = 1 (Fisher satisfies)
2. **Factor Reversal Test:** Price index x Quantity index = Value index (Fisher satisfies)

## Consumer Price Index (CPI)

Also called Cost of Living Index, it measures changes in the cost of living of a particular class of people.

### Methods of Construction

**1. Aggregative Expenditure Method:**
CPI = (Sigma P1.Q0 / Sigma P0.Q0) x 100

**2. Family Budget Method (Weighted Average of Relatives):**
CPI = Sigma(Price Relative x Weight) / Sigma(Weight)

### Steps in Construction
1. Determine the scope (which class of people)
2. Conduct family budget survey
3. Collect retail prices in base and current period
4. Select appropriate method
5. Calculate the index

### Uses of CPI
- Measurement of inflation
- Wage and salary adjustments (dearness allowance)
- Policy formulation
- Comparing living standards across regions
- Deflating national income (nominal to real values)

## Uses and Limitations of Index Numbers

### Uses
1. Measure changes in price level (inflation/deflation)
2. Help in wage policy (DA adjustment)
3. Guide government economic policy
4. Measure purchasing power of money
5. Deflate time series data
6. Useful for business planning and forecasting
7. International comparisons

### Limitations
1. Based on samples (may not be fully representative)
2. Choice of base year affects results
3. Changes in quality difficult to account for
4. Different formulas give different results
5. Weights become outdated over time
6. Limited to quantifiable variables
""",
    },
]


# ---------------------------------------------------------------------------
# Seeding Logic
# ---------------------------------------------------------------------------


async def seed_hierarchy(db):
    """Seed the content_hierarchy collection with board, stream, class, and subject documents."""
    collection = db["content_hierarchy"]
    print("\n=== Seeding Content Hierarchy ===")

    for key, doc in HIERARCHY.items():
        result = await collection.update_one(
            {"slug": doc["slug"], "type": doc["type"]},
            {"$set": doc, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        action = "updated" if result.matched_count > 0 else "created"
        print(f"  [{action}] {doc['type']}: {doc['name']}")

    print("  Content hierarchy seeding complete.")


async def seed_chapters(db):
    """Seed the knowledge_objects collection with chapter documents."""
    collection = db["knowledge_objects"]
    print("\n=== Seeding Knowledge Objects (Chapters) ===")
    print(f"  Total chapters to seed: {len(CHAPTERS)}")
    print()

    for i, chapter in enumerate(CHAPTERS, 1):
        doc = {
            "slug": chapter["slug"],
            "title": chapter["title"],
            "description": chapter["description"],
            "body_markdown": chapter["body_markdown"].strip(),
            "content_blocks": [],
            "metadata": {
                "board": "AHSEC",
                "class_level": "class-11",
                "subject": "economics",
                "chapter": chapter["slug"],
                "chapter_number": chapter["chapter_number"],
                "topic": ", ".join(chapter["topics"]),
                "topics": chapter["topics"],
                "difficulty": "medium",
                "language": "en",
                "estimated_read_time_minutes": max(5, len(chapter["body_markdown"]) // 1000),
                "keywords": chapter["keywords"],
            },
            "generated": {
                "mcqs": [],
                "summary": "",
                "definitions": [],
                "important_questions": [],
            },
            "derivative_hashes": {},
            "rendered_html": {},
            "status": "published",
            "published_at": datetime.now(timezone.utc),
            "last_pipeline_run": None,
            "page_views": 0,
            "search_impressions": 0,
            "updated_at": datetime.now(timezone.utc),
        }

        result = await collection.update_one(
            {"slug": chapter["slug"]},
            {"$set": doc, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        action = "updated" if result.matched_count > 0 else "created"
        print(f"  [{action}] Chapter {chapter['chapter_number']:2d}: {chapter['title']}")

    print(f"\n  Successfully seeded {len(CHAPTERS)} chapters.")


async def create_indexes(db):
    """Create useful indexes on the knowledge_objects collection."""
    collection = db["knowledge_objects"]
    print("\n=== Creating Indexes ===")

    # Unique slug index
    await collection.create_index("slug", unique=True, name="slug_unique")
    print("  Created index: slug_unique")

    # Compound index for URL resolution
    await collection.create_index(
        [
            ("metadata.board", 1),
            ("metadata.class_level", 1),
            ("metadata.subject", 1),
            ("metadata.chapter", 1),
        ],
        name="content_lookup",
    )
    print("  Created index: content_lookup")

    # Status + updated for admin listing
    await collection.create_index(
        [("status", 1), ("updated_at", -1)],
        name="status_updated",
    )
    print("  Created index: status_updated")

    # Hierarchy slug index
    hierarchy_collection = db["content_hierarchy"]
    await hierarchy_collection.create_index(
        [("slug", 1), ("type", 1)],
        unique=True,
        name="hierarchy_slug_type",
    )
    print("  Created index: hierarchy_slug_type")
    print("  Index creation complete.")


async def main():
    """Main entry point for the seeding script."""
    mongodb_uri = os.environ.get("MONGODB_URI")
    if not mongodb_uri:
        print("ERROR: MONGODB_URI environment variable is required.")
        print('Usage: MONGODB_URI="mongodb+srv://..." python3 scripts/seed-content.py')
        sys.exit(1)

    print("=" * 60)
    print("  AHSEC Class 11 Economics - Content Seeding Script")
    print("=" * 60)
    print(f"\nConnecting to MongoDB...")

    try:
        client = AsyncIOMotorClient(mongodb_uri, serverSelectionTimeoutMS=10000)
        # Verify connection
        await client.admin.command("ping")
        print("  Connected successfully.")
    except Exception as e:
        print(f"ERROR: Failed to connect to MongoDB: {e}")
        sys.exit(1)

    # Determine database name from URI or use default
    db_name = "aplus"
    if "/" in mongodb_uri:
        path_part = mongodb_uri.split("/")[-1].split("?")[0]
        if path_part and not path_part.startswith("?"):
            db_name = path_part
    db = client[db_name]
    print(f"  Using database: {db_name}")

    try:
        await seed_hierarchy(db)
        await seed_chapters(db)
        await create_indexes(db)
    except Exception as e:
        print(f"\nERROR during seeding: {e}")
        sys.exit(1)
    finally:
        client.close()

    print("\n" + "=" * 60)
    print("  Seeding complete! All content has been inserted/updated.")
    print("=" * 60)
    print()


if __name__ == "__main__":
    asyncio.run(main())
