import unittest

from core import extract_explicit_skill_names


class ExplicitRegressionTests(unittest.TestCase):
    def test_plugin_name_is_not_a_skill_request(self):
        self.assertEqual(extract_explicit_skill_names('ใช้ skill proof plugin ดูหน่อย'), ())

    def test_conjoined_requests_are_not_silently_lost(self):
        for query in ('ใช้สกิล pdf กับ xlsx', 'use skill pdf and xlsx'):
            with self.subTest(query=query):
                self.assertEqual(extract_explicit_skill_names(query), ('pdf', 'xlsx'))

    def test_catalog_distinguishes_next_skill_from_next_action(self):
        self.assertEqual(extract_explicit_skill_names('use skill pdf and summarize it', known_names=('pdf', 'xlsx')), ('pdf',))
        self.assertEqual(extract_explicit_skill_names('use skills pdf and xlsx', known_names=('pdf', 'xlsx')), ('pdf', 'xlsx'))
        self.assertEqual(extract_explicit_skill_names('$missing', known_names=('pdf',)), ('missing',))

    def test_repeated_skill_marker_in_conjunction(self):
        for query in ('use skill pdf and skill xlsx', 'ใช้สกิล pdf และสกิล xlsx'):
            self.assertEqual(extract_explicit_skill_names(query, known_names=('pdf', 'xlsx')), ('pdf', 'xlsx'))
