"""survey_report.py's research table, on a two-map survey. Needs an Archipelago checkout with the world in
worlds/bioshock on the path, as the report itself does (CI runs it from there); skipped otherwise."""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


@unittest.skipUnless(importlib.util.find_spec("worlds"), "needs an Archipelago checkout on the path")
class TestResearchTable(unittest.TestCase):
    def test_subjects_met_earlier_than_data_py_are_marked(self) -> None:
        result = subprocess.run([sys.executable, str(HERE / "survey_report.py"), str(HERE / "fixtures" /
                                 "survey_two_maps.json")], capture_output=True, text=True, check=True)
        rows = {line.split(" | ")[0].strip("| "): line for line in result.stdout.splitlines() if line.startswith("| ")}
        self.assertIn("Medical Pavilion (earlier)", rows["Bouncer"], "Bouncers are met before Neptune's Bounty")
        self.assertIn("Medical Pavilion (earlier) | Medical Pavilion, Neptune's Bounty", rows["Thuggish Splicer"])
        self.assertIn("Neptune's Bounty (earlier)", rows["Houdini Splicer"], "data.py waits for Hephaestus")
        self.assertIn("nowhere the survey can see", rows["Rosie"])


if __name__ == "__main__":
    unittest.main()
