"""The survey's command line: python -m unittest tools/map_reader/test_map_survey.py (no game files needed)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class TestCommandLine(unittest.TestCase):
    def test_an_empty_folder_gives_an_empty_list(self) -> None:
        # The JSON on standard output is what survey_report.py reads; once it came out empty (the CLI was lost).
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, str(HERE / "map_survey.py"), folder],
                                    capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [])


if __name__ == "__main__":
    unittest.main()
