"""Seed the facilities table from a CSV.

Usage:
    .venv/bin/python -m scripts.seed_facilities [csv_path]

data/facilities.csv is a small SAMPLE dataset (Abuja/Lagos/Kano) for
development — replace it with the full FMOH facility registry export
before evaluation.
"""

import sys

from app import facilities

if __name__ == "__main__":
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "data/facilities.csv"
    count = facilities.seed_facilities(csv_path)
    print(f"Seeded {count} facilities from {csv_path}")
