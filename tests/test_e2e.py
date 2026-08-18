import os
import subprocess
import sys
import tempfile
import unittest


class TestE2ECLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="blackbox_e2e_")
        self.db_path = os.path.join(self.temp_dir, "e2e_traces.db")
        self.env = os.environ.copy()
        # Ensure our local package is imported
        self.env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_agent_lifecycle(self):
        agent_script_path = os.path.join(self.temp_dir, "agent.py")
        with open(agent_script_path, "w") as f:
            f.write(f"""
import sys
import time
from blackbox_recorder import Tracer, BlackBoxConfig, SpanKind

config = BlackBoxConfig(db_path='{self.db_path}')
tracer = Tracer(config)

@tracer.trace("e2e_search", kind=SpanKind.TOOL)
def mock_search(q):
    return f"results for {{q}}"

with tracer.span("e2e_root", kind=SpanKind.AGENT) as root:
    mock_search("test query")
    with tracer.span("e2e_llm", kind=SpanKind.LLM) as llm:
        llm.set_llm_io(
            prompt="Hello",
            completion="World",
            thinking="Thinking about world...",
            prompt_tokens=5,
            completion_tokens=5,
            model="gemini-e2e"
        )
    
tracer.flush()
""")

        # 1. Run the script to generate traces
        subprocess.run([sys.executable, agent_script_path], check=True, env=self.env)

        # 2. Test CLI list
        res_list = subprocess.run(
            [sys.executable, "-m", "blackbox_recorder", "--db", self.db_path, "list"],
            capture_output=True, text=True, check=True, env=self.env
        )
        self.assertIn("e2e_root", res_list.stdout)
        self.assertIn("✅ OK", res_list.stdout)

        # Extract Trace ID from the last line (assuming it's a 32-char hex at the end)
        trace_id = None
        for line in res_list.stdout.splitlines():
            if "e2e_root" in line:
                parts = line.split()
                if len(parts[-1]) >= 32:
                    trace_id = parts[-1]
                    break
        
        self.assertIsNotNone(trace_id, "Could not find trace_id in CLI list output")

        # 3. Test CLI show verbose
        res_show = subprocess.run(
            [sys.executable, "-m", "blackbox_recorder", "--db", self.db_path, "show", trace_id, "-v"],
            capture_output=True, text=True, check=True, env=self.env
        )
        self.assertIn("e2e_root", res_show.stdout)
        self.assertIn("e2e_llm", res_show.stdout)
        self.assertIn("[gemini-e2e]", res_show.stdout)
        self.assertIn("💬 Prompt: Hello", res_show.stdout)
        self.assertIn("🧠 Thinking: Thinking about world...", res_show.stdout)
        self.assertIn("🤖 Response: World", res_show.stdout)

if __name__ == "__main__":
    unittest.main()
