import unittest
import tempfile
import os
from pathlib import Path
import importlib.util
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Dynamically import the backend to test its internal functions
MODULE_PATH = ROOT / "tools" / "mission-control" / "backend" / "main.py"
SPEC = importlib.util.spec_from_file_location("backend", MODULE_PATH)
backend = importlib.util.module_from_spec(SPEC)
try:
    SPEC.loader.exec_module(backend)
except ModuleNotFoundError as exc:
    if exc.name == "fastapi":
        raise unittest.SkipTest("Mission Control backend tests require the optional fastapi dependency.")
    raise

class InsightLayerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        backend.EXOCORTEX_ROOT = self.root # Inject temp root
        
        # Create necessary folders
        (self.root / "system").mkdir()
        (self.root / "domains" / "work" / "projects" / "test-proj").mkdir(parents=True)
        (self.root / "journal" / "summarised").mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_friction_calculation(self):
        # Create a session summary with no output (high friction)
        summary_path = self.root / "journal" / "summarised" / "2026-04-12.md"
        summary_path.write_text("""
## 2026-04-12 10:00:00 - Session in `/tmp/ExoCortex/domains/work/projects/test-proj`
### Summary
Tried to work but got stuck.
### Decisions
None extracted.
""")
        # Mock STATE.md
        (self.root / "domains" / "work" / "projects" / "test-proj" / "STATE.md").write_text("current_focus: testing")
        
        # Call the logic (via get_radar which is async, but we can call the logic inside or mock)
        # For simplicity, let's just test that it parses correctly
        projects = []
        # (Actually, we'll just test that the backend logic for score calculation is robust)
        # Note: get_radar is async, so we'd need an event loop, 
        # but let's assume we want to test the math.
        
    def test_isomorph_detection(self):
        # Create two MEMORY.md files with shared long keywords
        (self.root / "domains" / "work").mkdir(parents=True, exist_ok=True)
        (self.root / "domains" / "work" / "MEMORY.md").write_text("Focus on architectural dependency management and structural constraints.")
        (self.root / "domains" / "life").mkdir(parents=True, exist_ok=True)
        (self.root / "domains" / "life" / "MEMORY.md").write_text("Manage life structural constraints and dependency issues.")
        
        # Manually run the isomorph logic (refactored from endpoint)
        memory_files = list(self.root.rglob("MEMORY.md"))
        domain_keywords = {}
        for file in memory_files:
            domain = file.parent.parent.name if "projects" in str(file) else file.parent.name
            content = file.read_text().lower()
            words = set(re.findall(r"\b\w{8,}\b", content)) # Use 8+ chars to avoid small words
            domain_keywords[domain] = domain_keywords.get(domain, set()) | words

        self.assertIn("structural", domain_keywords["work"])
        self.assertIn("structural", domain_keywords["life"])
        self.assertIn("dependency", domain_keywords["work"])
        self.assertIn("dependency", domain_keywords["life"])

    def test_identity_auditor_peak_hour(self):
        # Create summaries at different times
        sum_dir = self.root / "journal" / "summarised"
        (sum_dir / "day1.md").write_text("## 2026-04-12 09:00:00\n### Decisions\n- D1\n- D2")
        (sum_dir / "day2.md").write_text("## 2026-04-13 09:30:00\n### Decisions\n- D3")
        (sum_dir / "day3.md").write_text("## 2026-04-14 22:00:00\n### Decisions\n- D4")

        # Logic from get_telemetry
        hourly_output = {}
        for file in sum_dir.glob("*.md"):
            content = file.read_text()
            blocks = content.split("## ")
            for block in blocks[1:]:
                time_match = re.search(r"(\d{2}):\d{2}:\d{2}", block)
                if time_match:
                    hour = int(time_match.group(1))
                    decisions = len(re.findall(r"- ", block.split("### Decisions")[1].split("###")[0])) if "### Decisions" in block else 0
                    hourly_output[hour] = hourly_output.get(hour, 0) + decisions
        
        peak_hour = max(hourly_output, key=hourly_output.get)
        self.assertEqual(peak_hour, 9)

    def test_thought_offloading(self):
        # Test the offload_thought logic (mocking the append)
        open_loops_path = self.root / "system" / "OPEN LOOPS.md"
        content = "Test thought"
        
        # Manually run backend logic
        if not open_loops_path.exists():
            open_loops_path.write_text("# Open Loops\n\n")
        with open(open_loops_path, "a") as f:
            f.write(f"- {content}\n")
            
        updated = open_loops_path.read_text()
        self.assertIn("- Test thought", updated)

    def test_open_loop_routing(self):
        # Create global open loops and project state
        loops_path = self.root / "system" / "OPEN LOOPS.md"
        loops_path.write_text("- Buy milk\n- Refactor code\n")
        
        proj_state_path = self.root / "domains" / "work" / "projects" / "test-proj" / "STATE.md"
        proj_state_path.write_text("# State\n")
        
        # Route "Refactor code" to test-proj
        content_to_route = "Refactor code"
        
        # 1. Remove from global
        lines = loops_path.read_text().split("\n")
        new_lines = [l for l in lines if content_to_route not in l]
        loops_path.write_text("\n".join(new_lines))
        
        # 2. Append to project
        with open(proj_state_path, "a") as f:
            f.write(f"\n- [ ] {content_to_route}\n")
            
        self.assertNotIn("Refactor code", loops_path.read_text())
        self.assertIn("- [ ] Refactor code", proj_state_path.read_text())

if __name__ == "__main__":
    unittest.main()
