#!/usr/bin/env python3
"""
Content Seeding Script for Syrabit.ai
Seeds the full educational content hierarchy into MongoDB:
  Board -> Class -> Stream -> Subject -> Chapter (with embedded Topics)

Supports AHSEC, SEBA, and Degree boards with real NCERT/AHSEC syllabus data.

Usage:
  # Seed all boards
  python seed-content.py --mongodb-uri "mongodb+srv://..."

  # Seed only AHSEC board
  python seed-content.py --board AHSEC

  # Dry run (preview without writing)
  python seed-content.py --dry-run --verbose

  # Uses MONGODB_URI env var by default
  export MONGODB_URI="mongodb+srv://..."
  python seed-content.py
"""
import argparse
import logging
import os
import re
import sys
from datetime import datetime, timezone
from uuid import uuid4

from pymongo import MongoClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DB_NAME = "syrabit_prod"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Convert text to URL-friendly slug (matches admin_content.py pattern)."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def now_utc():
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def make_topic(title: str) -> dict:
    """Create a topic dict matching the Topic embedded model."""
    return {
        "id": str(uuid4()),
        "title": title,
        "definition": None,
        "topic_slug": slugify(title),
        "definition_status": "pending",
    }


# ---------------------------------------------------------------------------
# Content Data
# ---------------------------------------------------------------------------

BOARDS_DATA = [
    {"name": "AHSEC", "slug": "ahsec"},
    {"name": "SEBA", "slug": "seba"},
    {"name": "Degree", "slug": "degree"},
]

AHSEC_CLASSES = ["HS 1st Year", "HS 2nd Year"]
AHSEC_STREAMS = ["Science", "Arts", "Commerce"]

SEBA_CLASSES = ["Class 9", "Class 10"]
SEBA_STREAMS = ["General"]

DEGREE_CLASSES = [
    "1st Semester", "2nd Semester", "3rd Semester",
    "4th Semester", "5th Semester", "6th Semester",
]
DEGREE_STREAMS = ["B.A.", "B.Com", "B.Sc"]

AHSEC_SUBJECTS = {
    "Science": ["Physics", "Chemistry", "Mathematics", "Biology", "English", "MIL (Assamese)"],
    "Arts": [
        "English", "MIL (Assamese)", "Political Science", "History",
        "Economics", "Education", "Logic & Philosophy",
    ],
    "Commerce": [
        "English", "MIL (Assamese)", "Accountancy", "Business Studies", "Economics",
    ],
}

# Chapter data: dict keyed by (board_class, subject) -> list of (chapter_title, [topics])
# Only subjects with detailed chapter data are listed; others get no chapters for now.

CHAPTERS_DATA = {}

# ---------------------------------------------------------------------------
# Physics HS 1st Year (15 chapters)
# ---------------------------------------------------------------------------
CHAPTERS_DATA[("HS 1st Year", "Physics")] = [
    ("Units and Measurements", [
        "International System of Units", "Measurement of Length",
        "Significant Figures", "Dimensional Analysis", "Errors in Measurement",
    ]),
    ("Motion in a Straight Line", [
        "Position and Displacement", "Average and Instantaneous Velocity",
        "Acceleration", "Kinematic Equations", "Relative Motion",
    ]),
    ("Motion in a Plane", [
        "Scalars and Vectors", "Vector Addition", "Resolution of Vectors",
        "Projectile Motion", "Uniform Circular Motion",
    ]),
    ("Laws of Motion", [
        "Newtons First Law", "Newtons Second Law", "Newtons Third Law",
        "Impulse and Momentum", "Friction", "Circular Motion Dynamics",
    ]),
    ("Work Energy and Power", [
        "Work Done by a Force", "Kinetic Energy", "Work-Energy Theorem",
        "Potential Energy", "Conservation of Energy", "Power", "Collisions",
    ]),
    ("System of Particles and Rotational Motion", [
        "Centre of Mass", "Linear Momentum of a System",
        "Moment of Inertia", "Torque", "Angular Momentum",
        "Equilibrium of Rigid Bodies",
    ]),
    ("Gravitation", [
        "Keplers Laws", "Universal Law of Gravitation",
        "Acceleration due to Gravity", "Gravitational Potential Energy",
        "Escape Velocity", "Orbital Velocity", "Satellites",
    ]),
    ("Mechanical Properties of Solids", [
        "Stress and Strain", "Hookes Law", "Youngs Modulus",
        "Bulk Modulus", "Shear Modulus", "Elastic Energy",
    ]),
    ("Mechanical Properties of Fluids", [
        "Pressure in Fluids", "Pascals Law", "Bernoullis Principle",
        "Viscosity", "Surface Tension", "Capillary Rise",
    ]),
    ("Thermal Properties of Matter", [
        "Temperature and Heat", "Thermal Expansion",
        "Specific Heat Capacity", "Calorimetry",
        "Change of State", "Heat Transfer",
    ]),
    ("Thermodynamics", [
        "Thermal Equilibrium", "Zeroth Law", "First Law of Thermodynamics",
        "Specific Heat Capacities of Gases", "Thermodynamic Processes",
        "Second Law of Thermodynamics", "Carnot Engine",
    ]),
    ("Kinetic Theory", [
        "Molecular Nature of Matter", "Gas Laws",
        "Kinetic Theory of an Ideal Gas", "Degrees of Freedom",
        "Mean Free Path", "Specific Heat Capacities",
    ]),
    ("Oscillations", [
        "Simple Harmonic Motion", "Energy in SHM",
        "Simple Pendulum", "Damped Oscillations",
        "Forced Oscillations and Resonance",
    ]),
    ("Waves", [
        "Transverse and Longitudinal Waves", "Speed of a Wave",
        "Principle of Superposition", "Standing Waves",
        "Beats", "Doppler Effect",
    ]),
]

# ---------------------------------------------------------------------------
# Chemistry HS 1st Year (14 chapters)
# ---------------------------------------------------------------------------
CHAPTERS_DATA[("HS 1st Year", "Chemistry")] = [
    ("Some Basic Concepts of Chemistry", [
        "Importance of Chemistry", "Laws of Chemical Combination",
        "Atomic and Molecular Masses", "Mole Concept",
        "Stoichiometry", "Percentage Composition",
    ]),
    ("Structure of Atom", [
        "Thomsons Model", "Rutherfords Model", "Bohrs Model",
        "Quantum Mechanical Model", "Quantum Numbers",
        "Electronic Configuration", "Aufbau Principle",
    ]),
    ("Classification of Elements and Periodicity", [
        "Genesis of Periodic Classification", "Modern Periodic Law",
        "Periodic Trends in Properties", "Ionization Enthalpy",
        "Electron Gain Enthalpy", "Electronegativity",
    ]),
    ("Chemical Bonding and Molecular Structure", [
        "Ionic Bond", "Covalent Bond", "Bond Parameters",
        "VSEPR Theory", "Valence Bond Theory",
        "Hybridization", "Molecular Orbital Theory",
    ]),
    ("Thermodynamics", [
        "System and Surroundings", "Internal Energy",
        "Enthalpy", "Hess Law", "Spontaneity",
        "Gibbs Energy", "Entropy",
    ]),
    ("Equilibrium", [
        "Equilibrium in Physical Processes", "Law of Chemical Equilibrium",
        "Le Chateliers Principle", "Ionic Equilibrium",
        "Acids and Bases", "Buffer Solutions", "Solubility Product",
    ]),
    ("Redox Reactions", [
        "Oxidation and Reduction", "Oxidation Number",
        "Balancing Redox Reactions", "Electrode Processes",
        "Electrochemical Cells",
    ]),
    ("Organic Chemistry - Some Basic Principles", [
        "Tetravalence of Carbon", "Structural Representations",
        "Classification of Organic Compounds", "Nomenclature",
        "Isomerism", "Reaction Mechanisms",
    ]),
    ("Hydrocarbons", [
        "Alkanes", "Alkenes", "Alkynes",
        "Aromatic Hydrocarbons", "Benzene Structure",
        "Carcinogenicity and Toxicity",
    ]),
    ("The s-Block Elements", [
        "Group 1 Elements - Alkali Metals", "General Properties",
        "Anomalous Properties of Lithium",
        "Group 2 Elements - Alkaline Earth Metals",
        "Important Compounds of Calcium",
    ]),
    ("The p-Block Elements", [
        "Group 13 Elements", "Boron Family",
        "Group 14 Elements", "Carbon Family",
        "Important Compounds of Silicon",
    ]),
    ("Hydrogen", [
        "Position of Hydrogen", "Dihydrogen",
        "Preparation and Properties", "Hydrides",
        "Water and Hydrogen Peroxide",
    ]),
    ("States of Matter", [
        "Intermolecular Forces", "Gas Laws",
        "Ideal Gas Equation", "Kinetic Molecular Theory",
        "Liquefaction of Gases", "Liquid State",
    ]),
    ("Environmental Chemistry", [
        "Environmental Pollution", "Atmospheric Pollution",
        "Water Pollution", "Soil Pollution",
        "Green Chemistry", "Strategies to Control Pollution",
    ]),
]

# ---------------------------------------------------------------------------
# Mathematics HS 1st Year (16 chapters)
# ---------------------------------------------------------------------------
CHAPTERS_DATA[("HS 1st Year", "Mathematics")] = [
    ("Sets", [
        "Sets and their Representations", "Types of Sets",
        "Subsets", "Venn Diagrams", "Operations on Sets",
    ]),
    ("Relations and Functions", [
        "Cartesian Product of Sets", "Relations",
        "Functions", "Domain and Range", "Algebra of Functions",
    ]),
    ("Trigonometric Functions", [
        "Angles and Their Measurement", "Trigonometric Functions",
        "Trigonometric Identities", "Trigonometric Equations",
        "Sum and Difference Formulas",
    ]),
    ("Complex Numbers", [
        "Algebra of Complex Numbers", "Modulus and Conjugate",
        "Argand Plane", "Polar Representation",
        "Quadratic Equations with Complex Roots",
    ]),
    ("Linear Inequalities", [
        "Algebraic Solutions of Linear Inequalities",
        "Graphical Representation", "System of Inequalities",
        "Solution of Linear Inequalities in Two Variables",
    ]),
    ("Permutations and Combinations", [
        "Fundamental Principle of Counting", "Permutations",
        "Combinations", "Factorial Notation",
        "Applications in Probability",
    ]),
    ("Binomial Theorem", [
        "Binomial Theorem for Positive Integers",
        "General and Middle Terms", "Pascals Triangle",
        "Applications of Binomial Theorem",
    ]),
    ("Sequences and Series", [
        "Arithmetic Progression", "Geometric Progression",
        "Arithmetic Mean", "Geometric Mean",
        "Sum to n Terms", "Infinite GP",
    ]),
    ("Straight Lines", [
        "Slope of a Line", "Various Forms of Equation of a Line",
        "Distance of a Point from a Line",
        "Angle Between Two Lines", "Family of Lines",
    ]),
    ("Conic Sections", [
        "Circles", "Parabola", "Ellipse", "Hyperbola",
        "Standard Equations", "Eccentricity",
    ]),
    ("Introduction to Three Dimensional Geometry", [
        "Coordinate Axes in 3D", "Distance Between Two Points",
        "Section Formula", "Coordinates of a Point in Space",
    ]),
    ("Limits and Derivatives", [
        "Intuitive Idea of Limits", "Limits of Polynomials",
        "Limits of Trigonometric Functions", "Derivatives",
        "Algebra of Derivatives",
    ]),
    ("Statistics", [
        "Measures of Dispersion", "Range",
        "Mean Deviation", "Variance and Standard Deviation",
        "Frequency Distribution Analysis",
    ]),
    ("Probability", [
        "Random Experiments", "Events", "Axiomatic Approach",
        "Addition Theorem", "Conditional Probability Basics",
    ]),
    ("Mathematical Reasoning", [
        "Statements", "Logical Connectives",
        "Implications", "Validating Statements",
        "Contradiction and Contrapositive",
    ]),
    ("Relations Between AM and GM", [
        "Relationship Between AM and GM",
        "Inequalities Involving AM and GM",
        "Special Sequences", "Applications",
    ]),
]

# ---------------------------------------------------------------------------
# Biology HS 1st Year (22 chapters)
# ---------------------------------------------------------------------------
CHAPTERS_DATA[("HS 1st Year", "Biology")] = [
    ("The Living World", [
        "Diversity of Living Organisms", "Taxonomic Categories",
        "Taxonomic Aids", "Nomenclature",
    ]),
    ("Biological Classification", [
        "Kingdom Monera", "Kingdom Protista", "Kingdom Fungi",
        "Kingdom Plantae", "Kingdom Animalia", "Viruses and Viroids",
    ]),
    ("Plant Kingdom", [
        "Algae", "Bryophytes", "Pteridophytes",
        "Gymnosperms", "Angiosperms", "Plant Life Cycles",
    ]),
    ("Animal Kingdom", [
        "Basis of Classification", "Phylum Porifera",
        "Phylum Cnidaria", "Phylum Arthropoda",
        "Phylum Chordata", "Classification of Chordates",
    ]),
    ("Morphology of Flowering Plants", [
        "The Root", "The Stem", "The Leaf",
        "The Flower", "The Fruit", "The Seed", "Floral Formula",
    ]),
    ("Anatomy of Flowering Plants", [
        "Tissues", "The Tissue System", "Anatomy of Dicot and Monocot Root",
        "Anatomy of Dicot and Monocot Stem", "Secondary Growth",
    ]),
    ("Structural Organisation in Animals", [
        "Animal Tissues", "Epithelial Tissue", "Connective Tissue",
        "Muscular Tissue", "Neural Tissue", "Organ Systems",
    ]),
    ("Cell - The Unit of Life", [
        "Cell Theory", "Prokaryotic Cell", "Eukaryotic Cell",
        "Cell Membrane", "Cell Organelles", "Nucleus",
    ]),
    ("Biomolecules", [
        "Carbohydrates", "Proteins", "Lipids",
        "Nucleic Acids", "Enzymes", "Metabolism",
    ]),
    ("Cell Cycle and Cell Division", [
        "Cell Cycle", "Mitosis", "Meiosis",
        "Significance of Meiosis", "Cytokinesis",
    ]),
    ("Photosynthesis in Higher Plants", [
        "Early Experiments", "Light Reactions",
        "Calvin Cycle", "Photorespiration", "C4 Pathway",
        "Factors Affecting Photosynthesis",
    ]),
    ("Respiration in Plants", [
        "Glycolysis", "Fermentation", "Krebs Cycle",
        "Electron Transport Chain", "Respiratory Quotient",
    ]),
    ("Plant Growth and Development", [
        "Growth Phases", "Plant Growth Regulators",
        "Auxins", "Gibberellins", "Photoperiodism", "Vernalisation",
    ]),
    ("Digestion and Absorption", [
        "Alimentary Canal", "Digestive Glands",
        "Digestion of Food", "Absorption", "Disorders of Digestive System",
    ]),
    ("Breathing and Exchange of Gases", [
        "Respiratory Organs", "Mechanism of Breathing",
        "Exchange of Gases", "Transport of Gases",
        "Respiratory Volumes", "Disorders",
    ]),
    ("Body Fluids and Circulation", [
        "Blood", "Lymph", "Human Heart",
        "Cardiac Cycle", "ECG", "Blood Vessels",
        "Disorders of Circulatory System",
    ]),
    ("Excretory Products and their Elimination", [
        "Human Excretory System", "Urine Formation",
        "Regulation of Kidney Function", "Micturition",
        "Disorders of the Excretory System",
    ]),
    ("Locomotion and Movement", [
        "Types of Movement", "Skeletal System",
        "Joints", "Muscular System", "Mechanism of Muscle Contraction",
        "Disorders of Muscular and Skeletal System",
    ]),
    ("Neural Control and Coordination", [
        "Neuron", "Central Nervous System", "Peripheral Nervous System",
        "Reflex Arc", "Sensory Receptors", "Eye and Ear",
    ]),
    ("Chemical Coordination and Integration", [
        "Endocrine Glands", "Hypothalamus", "Pituitary Gland",
        "Thyroid Gland", "Adrenal Gland",
        "Hormones of Heart Kidney and GI Tract",
    ]),
    ("Transport in Plants", [
        "Means of Transport", "Osmosis", "Plasmolysis",
        "Long Distance Transport", "Transpiration", "Uptake of Minerals",
    ]),
    ("Mineral Nutrition", [
        "Essential Mineral Elements", "Deficiency Symptoms",
        "Toxicity of Micronutrients", "Nitrogen Metabolism",
        "Nitrogen Fixation",
    ]),
]

# ---------------------------------------------------------------------------
# Physics HS 2nd Year (14 chapters)
# ---------------------------------------------------------------------------
CHAPTERS_DATA[("HS 2nd Year", "Physics")] = [
    ("Electric Charges and Fields", [
        "Electric Charge", "Coulombs Law", "Electric Field",
        "Electric Field Lines", "Electric Dipole", "Gauss Law",
    ]),
    ("Electrostatic Potential and Capacitance", [
        "Electrostatic Potential", "Potential due to Point Charge",
        "Equipotential Surfaces", "Capacitors", "Dielectrics",
        "Energy Stored in a Capacitor",
    ]),
    ("Current Electricity", [
        "Electric Current", "Ohms Law", "Resistivity",
        "Cells and EMF", "Kirchhoffs Rules", "Wheatstone Bridge",
    ]),
    ("Moving Charges and Magnetism", [
        "Magnetic Force on Current", "Biot-Savart Law",
        "Amperes Circuital Law", "Solenoid", "Torque on Current Loop",
        "Moving Coil Galvanometer",
    ]),
    ("Magnetism and Matter", [
        "Bar Magnet", "Earths Magnetism", "Magnetic Properties of Materials",
        "Diamagnetism", "Paramagnetism", "Ferromagnetism",
    ]),
    ("Electromagnetic Induction", [
        "Faradays Law", "Lenz Law", "Motional EMF",
        "Eddy Currents", "Inductance", "AC Generator",
    ]),
    ("Alternating Current", [
        "AC Voltage Applied to Resistor", "AC Through LCR Circuit",
        "Resonance", "Power in AC Circuit", "Transformers",
    ]),
    ("Electromagnetic Waves", [
        "Displacement Current", "Electromagnetic Spectrum",
        "Properties of EM Waves", "Applications of EM Waves",
    ]),
    ("Ray Optics and Optical Instruments", [
        "Reflection and Refraction", "Total Internal Reflection",
        "Lenses", "Prisms", "Optical Instruments",
        "Microscope and Telescope",
    ]),
    ("Wave Optics", [
        "Huygens Principle", "Interference", "Youngs Double Slit",
        "Diffraction", "Polarisation",
    ]),
    ("Dual Nature of Radiation and Matter", [
        "Photoelectric Effect", "Einsteins Photoelectric Equation",
        "de Broglie Hypothesis", "Davisson-Germer Experiment",
    ]),
    ("Atoms", [
        "Rutherfords Model", "Bohrs Model", "Hydrogen Spectrum",
        "de Broglie Explanation", "Line Spectra",
    ]),
    ("Nuclei", [
        "Nuclear Size and Composition", "Mass-Energy Relation",
        "Nuclear Binding Energy", "Radioactivity",
        "Nuclear Fission", "Nuclear Fusion",
    ]),
    ("Semiconductor Electronics", [
        "Semiconductors", "p-n Junction", "Diode as Rectifier",
        "Zener Diode", "Transistors", "Logic Gates",
    ]),
]

# ---------------------------------------------------------------------------
# Chemistry HS 2nd Year (16 chapters)
# ---------------------------------------------------------------------------
CHAPTERS_DATA[("HS 2nd Year", "Chemistry")] = [
    ("Solid State", [
        "Types of Solids", "Crystal Lattices", "Unit Cells",
        "Packing Efficiency", "Imperfections in Solids",
    ]),
    ("Solutions", [
        "Types of Solutions", "Concentration Units", "Raoults Law",
        "Colligative Properties", "Abnormal Molar Masses",
    ]),
    ("Electrochemistry", [
        "Electrochemical Cells", "Nernst Equation",
        "Conductance", "Electrolysis", "Batteries", "Corrosion",
    ]),
    ("Chemical Kinetics", [
        "Rate of Reaction", "Factors Affecting Rate",
        "Order of Reaction", "Molecularity",
        "Integrated Rate Equations", "Collision Theory",
    ]),
    ("Surface Chemistry", [
        "Adsorption", "Catalysis", "Colloids",
        "Emulsions", "Classification of Colloids",
    ]),
    ("General Principles and Processes of Isolation of Elements", [
        "Occurrence of Metals", "Concentration of Ores",
        "Extraction of Metals", "Thermodynamic Principles",
        "Electrochemical Principles", "Refining",
    ]),
    ("The p-Block Elements", [
        "Group 15 Elements", "Group 16 Elements",
        "Group 17 Elements", "Group 18 Elements",
        "Oxoacids", "Interhalogen Compounds",
    ]),
    ("The d and f Block Elements", [
        "Position in Periodic Table", "Electronic Configuration",
        "Properties of Transition Elements", "Lanthanoids",
        "Actinoids", "Important Compounds",
    ]),
    ("Coordination Compounds", [
        "Werner Theory", "IUPAC Nomenclature",
        "Isomerism", "Bonding in Coordination Compounds",
        "Crystal Field Theory", "Applications",
    ]),
    ("Haloalkanes and Haloarenes", [
        "Classification and Nomenclature", "Preparation",
        "Physical Properties", "Chemical Reactions",
        "SN1 and SN2 Mechanisms", "Polyhalogen Compounds",
    ]),
    ("Alcohols Phenols and Ethers", [
        "Classification", "Preparation of Alcohols",
        "Properties of Alcohols", "Phenols",
        "Ethers", "Reactions and Uses",
    ]),
    ("Aldehydes Ketones and Carboxylic Acids", [
        "Nomenclature", "Preparation of Aldehydes and Ketones",
        "Nucleophilic Addition", "Carboxylic Acids",
        "Reactions of Carboxylic Acids",
    ]),
    ("Amines", [
        "Classification", "Nomenclature", "Preparation",
        "Physical Properties", "Chemical Reactions", "Diazonium Salts",
    ]),
    ("Biomolecules", [
        "Carbohydrates", "Proteins", "Enzymes",
        "Vitamins", "Nucleic Acids", "Hormones",
    ]),
    ("Polymers", [
        "Classification of Polymers", "Addition Polymerisation",
        "Condensation Polymerisation", "Natural Polymers",
        "Biodegradable Polymers",
    ]),
    ("Chemistry in Everyday Life", [
        "Drugs and their Classification", "Drug-Target Interaction",
        "Chemicals in Food", "Cleansing Agents",
        "Soaps and Detergents",
    ]),
]

# ---------------------------------------------------------------------------
# Mathematics HS 2nd Year (13 chapters)
# ---------------------------------------------------------------------------
CHAPTERS_DATA[("HS 2nd Year", "Mathematics")] = [
    ("Relations and Functions", [
        "Types of Relations", "Types of Functions",
        "Composition of Functions", "Invertible Functions",
        "Binary Operations",
    ]),
    ("Inverse Trigonometric Functions", [
        "Basic Concepts", "Properties",
        "Principal Value Branch", "Graphs",
    ]),
    ("Matrices", [
        "Types of Matrices", "Operations on Matrices",
        "Transpose of a Matrix", "Symmetric and Skew-Symmetric",
        "Invertible Matrices",
    ]),
    ("Determinants", [
        "Determinant of a Matrix", "Properties of Determinants",
        "Area of a Triangle", "Adjoint and Inverse",
        "Applications of Determinants",
    ]),
    ("Continuity and Differentiability", [
        "Continuity", "Differentiability", "Chain Rule",
        "Implicit Differentiation", "Logarithmic Differentiation",
        "Mean Value Theorem",
    ]),
    ("Application of Derivatives", [
        "Rate of Change", "Increasing and Decreasing Functions",
        "Tangents and Normals", "Maxima and Minima", "Approximations",
    ]),
    ("Integrals", [
        "Integration as Inverse of Differentiation",
        "Methods of Integration", "Integration by Parts",
        "Definite Integrals", "Properties of Definite Integrals",
    ]),
    ("Application of Integrals", [
        "Area Under Curves", "Area Between Two Curves",
        "Volume of Revolution", "Applications in Physics",
    ]),
    ("Differential Equations", [
        "Order and Degree", "General and Particular Solutions",
        "Formation of DE", "Methods of Solving First Order DE",
        "Homogeneous Differential Equations",
    ]),
    ("Vector Algebra", [
        "Vectors and Scalars", "Addition of Vectors",
        "Scalar Product", "Vector Product", "Triple Product",
    ]),
    ("Three Dimensional Geometry", [
        "Direction Cosines", "Equation of a Line",
        "Angle Between Two Lines", "Equation of a Plane",
        "Distance of a Point from a Plane",
    ]),
    ("Linear Programming", [
        "Linear Programming Problem", "Graphical Method",
        "Types of Problems", "Feasible Region", "Optimal Solution",
    ]),
    ("Probability", [
        "Conditional Probability", "Multiplication Theorem",
        "Independent Events", "Bayes Theorem",
        "Random Variables", "Bernoulli Trials",
    ]),
]

# ---------------------------------------------------------------------------
# Accountancy HS 1st Year (12 chapters)
# ---------------------------------------------------------------------------
CHAPTERS_DATA[("HS 1st Year", "Accountancy")] = [
    ("Introduction to Accounting", [
        "Meaning and Objectives", "Accounting Process",
        "Qualitative Characteristics", "Branches of Accounting",
    ]),
    ("Theory Base of Accounting", [
        "Accounting Principles", "Accounting Concepts",
        "Accounting Conventions", "Accounting Standards",
    ]),
    ("Recording of Transactions", [
        "Business Transactions", "Journal Entries",
        "Rules of Debit and Credit", "Books of Original Entry",
        "Cash Book",
    ]),
    ("Ledger", [
        "Format of Ledger", "Posting from Journal",
        "Balancing of Accounts", "Types of Accounts",
    ]),
    ("Trial Balance", [
        "Objectives of Trial Balance", "Preparation",
        "Limitations", "Errors not Disclosed",
    ]),
    ("Bank Reconciliation Statement", [
        "Need for Reconciliation", "Causes of Difference",
        "Preparation of BRS", "Adjusted Cash Book",
    ]),
    ("Depreciation", [
        "Meaning and Causes", "Straight Line Method",
        "Written Down Value Method", "Accounting Treatment",
        "Disposal of Asset",
    ]),
    ("Bills of Exchange", [
        "Meaning and Features", "Drawing and Acceptance",
        "Discounting", "Endorsement", "Dishonour and Retirement",
    ]),
    ("Rectification of Errors", [
        "Types of Errors", "Errors Affecting Trial Balance",
        "Errors Not Affecting Trial Balance", "Suspense Account",
    ]),
    ("Financial Statements", [
        "Trading Account", "Profit and Loss Account",
        "Balance Sheet", "Adjustments", "Closing Entries",
    ]),
    ("Accounts from Incomplete Records", [
        "Meaning of Incomplete Records", "Statement of Affairs",
        "Distinction from Double Entry", "Ascertainment of Profit",
    ]),
    ("Computers in Accounting", [
        "Introduction to Computer", "Components of Computer",
        "Accounting Software", "Database Management",
        "Computerised Accounting System",
    ]),
]

# ---------------------------------------------------------------------------
# Business Studies HS 1st Year (10 chapters)
# ---------------------------------------------------------------------------
CHAPTERS_DATA[("HS 1st Year", "Business Studies")] = [
    ("Nature and Purpose of Business", [
        "Concept of Business", "Characteristics of Business",
        "Objectives of Business", "Classification of Business Activities",
        "Business Risk",
    ]),
    ("Forms of Business Organisation", [
        "Sole Proprietorship", "Partnership",
        "Hindu Undivided Family", "Cooperative Society",
        "Joint Stock Company",
    ]),
    ("Public Private and Global Enterprises", [
        "Public Sector Enterprises", "Private Sector Enterprises",
        "Global Enterprises", "Joint Ventures",
        "Public Private Partnership",
    ]),
    ("Business Services", [
        "Banking Services", "Insurance Services",
        "Transportation", "Warehousing", "Communication",
    ]),
    ("Emerging Modes of Business", [
        "E-Business", "E-Commerce", "Outsourcing BPO",
        "Online Trading", "Digital Payment",
    ]),
    ("Social Responsibility of Business", [
        "Concept of Social Responsibility", "Arguments For and Against",
        "Responsibility Towards Stakeholders",
        "Business Ethics", "Environmental Protection",
    ]),
    ("Formation of a Company", [
        "Stages in Formation", "Promotion",
        "Incorporation", "Subscription of Capital",
        "Commencement of Business",
    ]),
    ("Sources of Business Finance", [
        "Owners Funds", "Borrowed Funds",
        "Equity Shares", "Debentures",
        "Retained Earnings", "Trade Credit",
    ]),
    ("Small Business", [
        "Micro Small and Medium Enterprises",
        "Role of Small Business", "Government Schemes",
        "Problems of Small Business", "Startups",
    ]),
    ("Internal Trade", [
        "Wholesale Trade", "Retail Trade",
        "Types of Retail Trade", "Departmental Stores",
        "Chain Stores", "Franchise",
    ]),
]

# ---------------------------------------------------------------------------
# Economics HS 1st Year (9 chapters)
# ---------------------------------------------------------------------------
CHAPTERS_DATA[("HS 1st Year", "Economics")] = [
    ("Collection of Data", [
        "Primary and Secondary Data", "Methods of Collection",
        "Census and Sample Surveys", "Sources of Data",
    ]),
    ("Organisation of Data", [
        "Classification of Data", "Frequency Distribution",
        "Variables - Discrete and Continuous", "Tabulation",
    ]),
    ("Presentation of Data", [
        "Textual Presentation", "Tabular Presentation",
        "Diagrammatic Presentation", "Bar Diagrams", "Pie Charts",
    ]),
    ("Measures of Central Tendency", [
        "Arithmetic Mean", "Median", "Mode",
        "Relationship Between Mean Median Mode",
        "Weighted Mean",
    ]),
    ("Measures of Dispersion", [
        "Range", "Quartile Deviation", "Mean Deviation",
        "Standard Deviation", "Coefficient of Variation",
    ]),
    ("Correlation", [
        "Meaning and Types", "Scatter Diagram",
        "Karl Pearsons Method", "Spearmans Rank Correlation",
    ]),
    ("Index Numbers", [
        "Meaning and Uses", "Methods of Construction",
        "Laspeyres and Paasches Index", "Consumer Price Index",
    ]),
    ("Use of Statistical Tools", [
        "Statistical Tools in Economics", "Data Interpretation",
        "Limitations of Statistics", "Role of Statistics in Economics",
    ]),
    ("Indian Economy on the Eve of Independence", [
        "Low Level of Economic Development", "Agricultural Sector",
        "Industrial Sector", "Foreign Trade", "Demographic Profile",
    ]),
]


# ---------------------------------------------------------------------------
# Seeding Logic
# ---------------------------------------------------------------------------

class ContentSeeder:
    """Handles idempotent seeding of content hierarchy into MongoDB."""

    def __init__(self, db, dry_run=False, verbose=False):
        self.db = db
        self.dry_run = dry_run
        self.verbose = verbose
        self.counts = {
            "boards_created": 0,
            "boards_existing": 0,
            "classes_created": 0,
            "classes_existing": 0,
            "streams_created": 0,
            "streams_existing": 0,
            "subjects_created": 0,
            "subjects_existing": 0,
            "chapters_created": 0,
            "chapters_existing": 0,
            "topics_injected": 0,
        }

    def upsert_board(self, name: str, slug: str):
        """Upsert a board by slug. Returns the board's _id."""
        now = now_utc()
        if self.dry_run:
            logger.info(f"  [DRY RUN] Would upsert board: {name} (slug={slug})")
            self.counts["boards_created"] += 1
            return f"dry-run-{slug}"

        result = self.db.boards.find_one({"slug": slug})
        if result:
            self.counts["boards_existing"] += 1
            if self.verbose:
                logger.info(f"  Board exists: {name}")
            return result["_id"]

        doc = {
            "name": name,
            "slug": slug,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        insert_result = self.db.boards.insert_one(doc)
        self.counts["boards_created"] += 1
        logger.info(f"  Created board: {name}")
        return insert_result.inserted_id

    def upsert_class(self, name: str, board_id):
        """Upsert a class by name + board_id. Returns the class's _id."""
        now = now_utc()
        if self.dry_run:
            logger.info(f"    [DRY RUN] Would upsert class: {name}")
            self.counts["classes_created"] += 1
            return f"dry-run-class-{slugify(name)}"

        result = self.db.classes.find_one({"name": name, "board_id": board_id})
        if result:
            self.counts["classes_existing"] += 1
            if self.verbose:
                logger.info(f"    Class exists: {name}")
            return result["_id"]

        doc = {
            "name": name,
            "board_id": board_id,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        insert_result = self.db.classes.insert_one(doc)
        self.counts["classes_created"] += 1
        logger.info(f"    Created class: {name}")
        return insert_result.inserted_id

    def upsert_stream(self, name: str, class_id):
        """Upsert a stream by name + class_id. Returns the stream's _id."""
        now = now_utc()
        if self.dry_run:
            logger.info(f"      [DRY RUN] Would upsert stream: {name}")
            self.counts["streams_created"] += 1
            return f"dry-run-stream-{slugify(name)}"

        result = self.db.streams.find_one({"name": name, "class_id": class_id})
        if result:
            self.counts["streams_existing"] += 1
            if self.verbose:
                logger.info(f"      Stream exists: {name}")
            return result["_id"]

        doc = {
            "name": name,
            "class_id": class_id,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        insert_result = self.db.streams.insert_one(doc)
        self.counts["streams_created"] += 1
        logger.info(f"      Created stream: {name}")
        return insert_result.inserted_id

    def upsert_subject(self, name: str, stream_id):
        """Upsert a subject by name + stream_id. Returns the subject's _id."""
        now = now_utc()
        if self.dry_run:
            logger.info(f"        [DRY RUN] Would upsert subject: {name}")
            self.counts["subjects_created"] += 1
            return f"dry-run-subject-{slugify(name)}"

        result = self.db.subjects.find_one({"name": name, "stream_id": stream_id})
        if result:
            self.counts["subjects_existing"] += 1
            if self.verbose:
                logger.info(f"        Subject exists: {name}")
            return result["_id"]

        doc = {
            "name": name,
            "stream_id": stream_id,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        insert_result = self.db.subjects.insert_one(doc)
        self.counts["subjects_created"] += 1
        logger.info(f"        Created subject: {name}")
        return insert_result.inserted_id

    def upsert_chapter(self, title: str, subject_id, chapter_number: int, topics: list):
        """Upsert a chapter by slug + subject_id. Returns the chapter's _id."""
        now = now_utc()
        slug = slugify(title)
        topic_dicts = [make_topic(t) for t in topics]

        if self.dry_run:
            if self.verbose:
                logger.info(
                    f"          [DRY RUN] Would upsert chapter #{chapter_number}: "
                    f"{title} ({len(topics)} topics)"
                )
            self.counts["chapters_created"] += 1
            self.counts["topics_injected"] += len(topics)
            return f"dry-run-chapter-{slug}"

        result = self.db.chapters.find_one({"slug": slug, "subject_id": subject_id})
        if result:
            self.counts["chapters_existing"] += 1
            self.counts["topics_injected"] += len(result.get("published_topics", []))
            if self.verbose:
                logger.info(f"          Chapter exists: {title}")
            return result["_id"]

        doc = {
            "title": title,
            "slug": slug,
            "subject_id": subject_id,
            "chapter_number": chapter_number,
            "status": "draft",
            "content_en": None,
            "content_as": None,
            "meta_description": None,
            "keywords": None,
            "word_count": None,
            "published_topics": topic_dicts,
            "faq_jsonld": None,
            "created_at": now,
            "updated_at": now,
        }
        insert_result = self.db.chapters.insert_one(doc)
        self.counts["chapters_created"] += 1
        self.counts["topics_injected"] += len(topic_dicts)
        if self.verbose:
            logger.info(
                f"          Created chapter #{chapter_number}: {title} "
                f"({len(topic_dicts)} topics)"
            )
        return insert_result.inserted_id

    def seed_board_ahsec(self):
        """Seed the AHSEC board with all classes, streams, subjects, and chapters."""
        logger.info("Seeding AHSEC board...")
        board_id = self.upsert_board("AHSEC", "ahsec")

        for class_name in AHSEC_CLASSES:
            class_id = self.upsert_class(class_name, board_id)

            for stream_name in AHSEC_STREAMS:
                stream_id = self.upsert_stream(stream_name, class_id)

                subjects = AHSEC_SUBJECTS.get(stream_name, [])
                for subject_name in subjects:
                    subject_id = self.upsert_subject(subject_name, stream_id)

                    # Check if we have chapter data for this class+subject
                    chapter_key = (class_name, subject_name)
                    chapters = CHAPTERS_DATA.get(chapter_key, [])
                    for idx, (ch_title, ch_topics) in enumerate(chapters, start=1):
                        self.upsert_chapter(ch_title, subject_id, idx, ch_topics)

    def seed_board_seba(self):
        """Seed the SEBA board with classes and streams."""
        logger.info("Seeding SEBA board...")
        board_id = self.upsert_board("SEBA", "seba")

        for class_name in SEBA_CLASSES:
            class_id = self.upsert_class(class_name, board_id)

            for stream_name in SEBA_STREAMS:
                self.upsert_stream(stream_name, class_id)

    def seed_board_degree(self):
        """Seed the Degree board with semesters and streams."""
        logger.info("Seeding Degree board...")
        board_id = self.upsert_board("Degree", "degree")

        for class_name in DEGREE_CLASSES:
            class_id = self.upsert_class(class_name, board_id)

            for stream_name in DEGREE_STREAMS:
                self.upsert_stream(stream_name, class_id)

    def seed_all(self, board_filter=None):
        """Seed all boards or a specific board."""
        if board_filter:
            board_filter_lower = board_filter.lower()
            if board_filter_lower == "ahsec":
                self.seed_board_ahsec()
            elif board_filter_lower == "seba":
                self.seed_board_seba()
            elif board_filter_lower == "degree":
                self.seed_board_degree()
            else:
                logger.error(f"Unknown board filter: {board_filter}")
                logger.error("Valid options: AHSEC, SEBA, Degree")
                sys.exit(1)
        else:
            self.seed_board_ahsec()
            self.seed_board_seba()
            self.seed_board_degree()

    def print_summary(self):
        """Print a summary of all operations performed."""
        logger.info("=" * 60)
        logger.info("CONTENT SEEDING SUMMARY")
        logger.info("=" * 60)
        logger.info(f"  Boards created:    {self.counts['boards_created']}")
        logger.info(f"  Boards existing:   {self.counts['boards_existing']}")
        logger.info(f"  Classes created:   {self.counts['classes_created']}")
        logger.info(f"  Classes existing:  {self.counts['classes_existing']}")
        logger.info(f"  Streams created:   {self.counts['streams_created']}")
        logger.info(f"  Streams existing:  {self.counts['streams_existing']}")
        logger.info(f"  Subjects created:  {self.counts['subjects_created']}")
        logger.info(f"  Subjects existing: {self.counts['subjects_existing']}")
        logger.info(f"  Chapters created:  {self.counts['chapters_created']}")
        logger.info(f"  Chapters existing: {self.counts['chapters_existing']}")
        logger.info(f"  Topics injected:   {self.counts['topics_injected']}")
        logger.info("=" * 60)
        if self.dry_run:
            logger.info("(Dry run - no documents were actually written)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Seed educational content hierarchy into MongoDB for syrabit.ai"
    )
    parser.add_argument(
        "--mongodb-uri",
        default=os.environ.get("MONGODB_URI"),
        help="MongoDB connection URI (or set MONGODB_URI env var)",
    )
    parser.add_argument(
        "--board",
        default=None,
        help="Seed only a specific board: AHSEC, SEBA, or Degree",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be created without writing to MongoDB",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed output including existing documents",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # In dry-run mode, we do not need a MongoDB connection
    if args.dry_run:
        logger.info("Running in DRY RUN mode (no MongoDB connection needed)")
        seeder = ContentSeeder(db=None, dry_run=True, verbose=args.verbose)
        seeder.seed_all(board_filter=args.board)
        seeder.print_summary()
        return

    # Validate MongoDB URI
    mongodb_uri = args.mongodb_uri
    if not mongodb_uri:
        logger.error("MongoDB URI is required.")
        logger.error("Set MONGODB_URI env var or pass --mongodb-uri.")
        sys.exit(1)

    # Connect to MongoDB
    logger.info("Connecting to MongoDB...")
    client = MongoClient(mongodb_uri)
    db = client[DB_NAME]

    # Verify connection
    try:
        client.admin.command("ping")
        logger.info(f"Connected to MongoDB (database: {DB_NAME})")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        sys.exit(1)

    # Run seeding
    seeder = ContentSeeder(db=db, dry_run=False, verbose=args.verbose)
    seeder.seed_all(board_filter=args.board)
    seeder.print_summary()

    # Cleanup
    client.close()
    logger.info("Done.")


if __name__ == "__main__":
    main()
