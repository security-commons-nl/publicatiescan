"""Zorgt dat `import avgscan` werkt, ongeacht hoe pytest wordt aangeroepen.

`python -m pytest` zet de huidige map zelf in sys.path; een kale `pytest tests/` doet dat
niet en voegt alleen tests/ toe. Zonder deze conftest slaagt de suite lokaal en faalt hij
in CI met ModuleNotFoundError. Pytest laadt een conftest.py in de repo-root altijd, en
neemt de map ervan mee in sys.path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
