import importlib.util
from pathlib import Path
import unittest

SERVER = Path(__file__).resolve().parents[1] / "backend" / "server.py"
spec = importlib.util.spec_from_file_location("server", SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class MaterialRulesTest(unittest.TestCase):
    def test_missing_required_fields_blocks_generation(self):
        result = server.analyze_payload({"genre": "work_plan", "fields": {}, "facts": ""})
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any(i["code"] == "missing_field" for i in result["issues"]))
        self.assertTrue(any(i["code"] == "missing_facts" for i in result["issues"]))

    def test_vague_phrase_without_execution_guard_fails(self):
        fields = {name: "已填" for name in server.RULES["genres"]["work_plan"]["required_fields"]}
        draft = "总体要求\n要加强组织领导，形成工作合力，确保取得实效。"
        result = server.analyze_payload({"genre": "work_plan", "fields": fields, "facts": "会议要求推进相关工作", "draft": draft})
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(i["code"] == "vague_without_guard" for i in result["issues"]))

    def test_unbound_data_claim_fails_without_evidence(self):
        fields = {name: "已填" for name in server.RULES["genres"]["work_report"]["required_fields"]}
        draft = "工作成效\n2025年完成整改12项，覆盖率达到95%。"
        result = server.analyze_payload({"genre": "work_report", "fields": fields, "facts": "有整改工作", "draft": draft, "evidence": []})
        self.assertTrue(any(i["code"] == "unbound_claim" for i in result["issues"]))


if __name__ == "__main__":
    unittest.main()
