import unittest

from app.core.oreno3d_mapping import Oreno3DIwaraMapping, normalize_mapping_key


class Oreno3DIwaraMappingTests(unittest.TestCase):
    def test_bundled_map_contains_unique_large_dictionary(self):
        mapping = Oreno3DIwaraMapping()
        self.assertEqual(mapping.count, 1040)
        self.assertEqual(mapping.alias_count, 3470)
        self.assertEqual(mapping.ambiguous_count, 46)
        self.assertEqual(mapping.count, len({normalize_mapping_key(key) for key in mapping._entries}))

    def test_known_terms_resolve_to_their_typed_numeric_entities(self):
        mapping = Oreno3DIwaraMapping()
        azur = mapping.resolve("azur_lane")
        self.assertIsNotNone(azur)
        self.assertEqual((azur.kind, azur.entity_id, azur.route), ("origin", "14", "origin:14"))
        self.assertEqual(
            (mapping.resolve("碧蓝航线").kind, mapping.resolve("碧蓝航线").entity_id),
            ("origin", "14"),
        )
        ahegao = mapping.resolve("ahegao")
        self.assertIsNotNone(ahegao)
        self.assertEqual((ahegao.kind, ahegao.entity_id), ("tag", "164"))

    def test_ambiguous_and_unknown_terms_are_not_guessed(self):
        mapping = Oreno3DIwaraMapping()
        self.assertIsNone(mapping.resolve("elf"))
        self.assertIsNone(mapping.resolve("1234"))
        self.assertIsNone(mapping.resolve("alice"))


if __name__ == "__main__":
    unittest.main()
