"""ЖИ моделін үш бөлек тақырыптағы бақылау жиынымен тексеру."""
import unittest
from decision import MODEL, rank

SETS = {
    'Оқу': [('Тез жібер, бүгін мерзім бітеді', True), ('Келесі аптада кездесеміз бе', False)],
    'Техникалық қолдау': [('Жүйеде қате шықты, шұғыл тексер', True), ('Кейін қарап шық, асықпай', False)],
    'Күнделікті байланыс': [('Дереу хабарлас, көмек керек', True), ('Сәлем, ертең сөйлесеміз', False)],
}


class DecisionTests(unittest.TestCase):
    def test_three_datasets(self):
        for group, samples in SETS.items():
            for sentence, expected in samples:
                with self.subTest(group=group, sentence=sentence):
                    p, signals = MODEL.predict(sentence)
                    self.assertEqual(p >= .5, expected)
                    self.assertTrue(signals)

    def test_unknown_is_neutral(self):
        self.assertEqual(MODEL.predict('абстракция'), (.5, []))

    def test_priority_and_own_message_exclusion(self):
        rows = rank([
            {'id': 1, 'sender': 2, 'body': 'Ертең хабарлас', 'sent': 0},
            {'id': 2, 'sender': 2, 'body': 'Шұғыл көмек керек', 'sent': 86400},
            {'id': 3, 'sender': 1, 'body': 'Дереу жауап берші', 'sent': 86400},
        ], 1, 86400)
        self.assertEqual([row[-1]['id'] for row in rows], [2, 1])


if __name__ == '__main__':
    unittest.main()

