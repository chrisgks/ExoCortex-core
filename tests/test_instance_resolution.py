import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import instance


def make_instance(base: Path) -> Path:
    """Create a directory that looks like an ExoCortex instance."""
    (base / "system").mkdir(parents=True)
    (base / "journal").mkdir()
    (base / "wiki").mkdir()
    return base


class InstanceResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        # Clear EXOCORTEX_HOME so cases control it explicitly.
        self._saved_home = os.environ.pop("EXOCORTEX_HOME", None)

    def tearDown(self) -> None:
        if self._saved_home is not None:
            os.environ["EXOCORTEX_HOME"] = self._saved_home
        else:
            os.environ.pop("EXOCORTEX_HOME", None)

    def test_explicit_arg_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inst = make_instance(Path(tmp) / "explicit")
            os.environ["EXOCORTEX_HOME"] = str(Path(tmp) / "env-home")
            resolved = instance.resolve_instance_root(str(inst))
            self.assertEqual(resolved, inst.resolve())

    def test_env_home_used_when_no_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inst = make_instance(Path(tmp) / "env-home")
            os.environ["EXOCORTEX_HOME"] = str(inst)
            resolved = instance.resolve_instance_root(None)
            self.assertEqual(resolved, inst.resolve())

    def test_cwd_walk_finds_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inst = make_instance(Path(tmp) / "data")
            nested = inst / "domains" / "work"
            nested.mkdir(parents=True)
            with mock.patch.object(instance.Path, "cwd", return_value=nested):
                resolved = instance.resolve_instance_root(None)
            self.assertEqual(resolved, inst.resolve())

    def test_falls_back_to_engine_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # cwd has no instance markers above it.
            empty = Path(tmp) / "nowhere"
            empty.mkdir()
            with mock.patch.object(instance.Path, "cwd", return_value=empty):
                resolved = instance.resolve_instance_root(None)
            self.assertEqual(resolved, instance.ENGINE_ROOT)

    def test_unrelated_dir_with_one_marker_not_an_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            partial = Path(tmp) / "partial"
            (partial / "system").mkdir(parents=True)  # only one marker
            self.assertFalse(instance._looks_like_instance(partial))

    def test_env_home_tilde_expansion(self) -> None:
        os.environ["EXOCORTEX_HOME"] = "~/some-exo-instance"
        resolved = instance.resolve_instance_root(None)
        self.assertEqual(resolved, (Path.home() / "some-exo-instance").resolve())


if __name__ == "__main__":
    unittest.main()
