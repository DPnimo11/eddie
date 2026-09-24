import tempfile
import unittest
from pathlib import Path

import eddie


class TermTests(unittest.TestCase):
    def test_fa26(self):
        term = eddie.parse_term("fa26")
        self.assertEqual(term.year, "2026")
        self.assertIn("fall", term.sessions)

    def test_invalid_term(self):
        with self.assertRaises(eddie.EddieError):
            eddie.parse_term("2026")


class CourseSelectionTests(unittest.TestCase):
    def setUp(self):
        self.courses = [
            {"id": 10, "code": "CIS 3200", "name": "Algorithms", "year": "2026", "session": "Fall"},
            {"id": 11, "code": "CIS 3200", "name": "Algorithms", "year": "2025", "session": "Fall"},
            {"id": 12, "code": "CIS 4500", "name": "Databases", "year": "2026", "session": "Fall"},
        ]

    def test_code_normalization_and_term(self):
        selected = eddie.select_courses(self.courses, ["cis3200"], [], eddie.parse_term("fa26"))
        self.assertEqual([course["id"] for course in selected], [10])

    def test_section_suffix_can_be_omitted(self):
        courses = [
            {"id": 10, "code": "CIS 3200 001", "name": "Algorithms", "year": "2026", "session": "Fall"}
        ]
        selected = eddie.select_courses(courses, ["CIS3200"], [], eddie.parse_term("fa26"))
        self.assertEqual([course["id"] for course in selected], [10])

    def test_ambiguous_without_term(self):
        with self.assertRaises(eddie.EddieError):
            eddie.select_courses(self.courses, ["CIS 3200"], [], None)


class ExportTests(unittest.TestCase):
    def test_identity_fields_are_omitted_recursively(self):
        result = eddie.export_thread(
            {
                "id": 5,
                "number": 2,
                "title": "Q",
                "user_id": 999,
                "answers": [
                    {
                        "id": 6,
                        "content": "A",
                        "user_id": 998,
                        "comments": [{"id": 7, "content": "C", "user_id": 997}],
                    }
                ],
            },
            10,
        )
        serialized = repr(result)
        self.assertNotIn("user_id", serialized)
        self.assertNotIn("999", serialized)
        self.assertEqual(result["answers"][0]["comments"][0]["content"], "C")

    def test_atomic_json_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "value.json"
            eddie.write_json_atomic(path, {"ok": True})
            self.assertIn('"ok": true', path.read_text(encoding="utf-8"))
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
