"""Tests for tools/release.py, on a copy of the files it edits in a scratch folder: python -m unittest tools/test_release.py"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("release", HERE / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
REAL = {name: getattr(release, name) for name in ("ROOT", "WORLD", "DATA", "MANIFEST", "AGENTS")}


class TestRelease(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        world = self.root / "apworld" / "bioshock"
        (world / "client").mkdir(parents=True)
        (self.root / "client-poc").mkdir()
        (world / "data.py").write_text('GAME_NAME = "BioShock"\nWORLD_VERSION = "0.2.0"  # comment\n')
        (world / "archipelago.json").write_text('{\n\t"game": "BioShock",\n\t"world_version": "0.2.0"\n}\n')
        agents = (world / "client" / "agent.js", self.root / "client-poc" / "bioshock_ap_agent.js")
        for agent in agents:
            agent.write_text("'use strict';\n\nconst AGENT_VERSION = '2026-10-24.6';\n")
        for name, value in (("ROOT", self.root), ("WORLD", world), ("DATA", world / "data.py"),
                            ("MANIFEST", world / "archipelago.json"), ("AGENTS", agents)):
            setattr(release, name, value)
        self.addCleanup(lambda: [setattr(release, name, value) for name, value in REAL.items()])

    def test_reads_every_version(self) -> None:
        found = release.read_versions()
        self.assertEqual(found, {
            "data.py": "0.2.0",
            "archipelago.json": "0.2.0",
            "apworld/bioshock/client/agent.js": "2026-10-24.6",
            "client-poc/bioshock_ap_agent.js": "2026-10-24.6",
        })
        self.assertEqual(release.problems(found), [])

    def test_setting_the_world_version_changes_both_files(self) -> None:
        release.set_world("0.2.1")
        found = release.read_versions()
        self.assertEqual((found["data.py"], found["archipelago.json"]), ("0.2.1", "0.2.1"))
        self.assertIn('\t"game"', release.MANIFEST.read_text(), "the manifest keeps its tabs")
        self.assertIn("# comment", release.DATA.read_text())

    def test_wrong_formats_are_refused(self) -> None:
        for call, version in ((release.set_world, "1.2"), (release.set_world, "v0.2.1"),
                              (release.set_agent, "2026-10-25"), (release.set_agent, "25-10-2026.1")):
            with self.subTest(version=version), self.assertRaises(SystemExit):
                call(version)
        self.assertEqual(release.read_versions()["data.py"], "0.2.0", "nothing changed")

    def test_agent_copies_that_disagree_are_a_problem(self) -> None:
        second = release.AGENTS[1]
        second.write_text(second.read_text().replace("2026-10-24.6", "2026-10-24.7"))
        problems = release.problems(release.read_versions())
        self.assertEqual(len(problems), 1)
        self.assertIn("agent's versions disagree", problems[0])
        release.set_agent("2026-10-25.1")
        self.assertEqual(release.problems(release.read_versions()), [])

    def test_build_refuses_a_link_to_another_world(self) -> None:
        ap = self.archipelago()
        other = self.root / "other"
        other.mkdir()
        (ap / "worlds" / "bioshock").symlink_to(other)
        with self.assertRaises(SystemExit) as refused:
            release.build(ap)
        self.assertIn("not to this repository's world", str(refused.exception.code))
        self.assertTrue(other.exists())

    def test_build_refuses_a_target_that_holds_the_repository(self) -> None:
        ap = self.archipelago()
        inside = ap / "worlds" / "bioshock"
        shutil.copytree(self.root / "apworld", inside / "apworld")
        release.WORLD = inside / "apworld" / "bioshock"  # the repository cloned at worlds/bioshock
        with self.assertRaises(SystemExit) as refused:
            release.build(ap)
        self.assertIn("holds this repository itself", str(refused.exception.code))
        self.assertTrue(release.WORLD.exists(), "nothing deleted")

    def archipelago(self) -> Path:
        ap = self.root / "ap"
        (ap / "worlds").mkdir(parents=True)
        (ap / "Launcher.py").touch()
        return ap


if __name__ == "__main__":
    unittest.main()
