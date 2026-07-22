import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PortableWorkflowTests(unittest.TestCase):
    def test_portable_build_bundles_and_smokes_wizard(self):
        workflow = (ROOT / ".github/workflows/portable-executables.yml").read_text()
        self.assertIn("python -m pip install '.[wizard]'", workflow)
        self.assertIn("--collect-all keyring", workflow)
        self.assertIn("--collect-all uvicorn", workflow)
        self.assertIn("scripts/smoke-portable-wizard.sh ./dist/openai-tunnel-kit", workflow)

    def test_portable_wizard_smoke_exercises_ui_and_wallet_support(self):
        smoke = (ROOT / "scripts/smoke-portable-wizard.sh").read_text()
        self.assertIn("wizard --no-browser", smoke)
        self.assertIn("<h1>openai-tunnel-kit wizard</h1>", smoke)
        self.assertIn("api/admin-credential/forget", smoke)
        self.assertIn("system-wallet support is missing", smoke)


if __name__ == "__main__":
    unittest.main()
