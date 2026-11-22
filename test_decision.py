"""ЖИ моделін үш бөлек тақырыптағы бақылау жиынымен тексеру."""
import unittest
from decision import (DEFAULT_WEIGHTS, HOLDOUT, MODEL, TRAIN, demo_messages,
                      evaluate_model, rank, validate_weights, words)

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

    def test_weight_normalization_and_immutable_defaults(self):
        weights = validate_weights({'urgency': 65, 'wait': 25, 'recent': 10})
        for key in weights:
            self.assertAlmostEqual(weights[key], DEFAULT_WEIGHTS[key])
        weights['urgency'] = 1
        self.assertEqual(DEFAULT_WEIGHTS['urgency'], .65)
        huge = validate_weights({'urgency': 1e308, 'wait': 1e308, 'recent': 1e308})
        self.assertAlmostEqual(sum(huge.values()), 1)

    def test_invalid_weights_are_rejected(self):
        invalid = [
            {}, {'urgency': 1}, [1, 0, 0],
            {'urgency': 0, 'wait': 0, 'recent': 0},
            {'urgency': -1, 'wait': 1, 'recent': 1},
            {'urgency': float('nan'), 'wait': 1, 'recent': 1},
            {'urgency': float('inf'), 'wait': 1, 'recent': 1},
            {'urgency': '1', 'wait': 1, 'recent': 1},
            {'urgency': True, 'wait': 1, 'recent': 1},
            {'urgency': 1, 'wait': 1, 'recent': 1, 'extra': 1},
        ]
        for weights in invalid:
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                rank([], 1, 0, weights)

    def test_weight_changes_choose_different_alternatives(self):
        now = 100000
        messages = demo_messages(now)
        urgent = rank(messages, 1, now, {'urgency': 1, 'wait': 0, 'recent': 0})
        oldest = rank(messages, 1, now, {'urgency': 0, 'wait': 1, 'recent': 0})
        self.assertEqual(oldest[0][-1]['id'], 2)
        self.assertNotEqual(urgent[0][-1]['id'], oldest[0][-1]['id'])
        self.assertGreater(urgent[0][1], urgent[-1][1])

    def test_score_decomposition_and_bounded_time_criteria(self):
        now = 100000
        messages = demo_messages(now)
        messages += [
            {'id': 4, 'sender': 2, 'body': 'абстракция', 'sent': now + 1000},
            {'id': 5, 'sender': 2, 'body': 'абстракция', 'sent': now - 900000},
        ]
        weights = validate_weights({'urgency': 2, 'wait': 3, 'recent': 5})
        rows = rank(messages, 1, now, weights)
        self.assertEqual({row[-1]['id'] for row in rows}, {1, 2, 3, 4, 5})
        for score, probability, wait, recent, _, message in rows:
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)
            expected = 100 * (weights['urgency'] * probability + weights['wait'] * wait
                              + weights['recent'] * recent)
            self.assertAlmostEqual(score, expected)
            if message['id'] == 4:
                self.assertEqual((wait, recent), (0, 1))
            if message['id'] == 5:
                self.assertEqual((wait, recent), (1, 0))

    def test_holdout_is_separate_and_metrics_match_predictions(self):
        training = {tuple(words(text)) for examples in TRAIN.values() for text in examples}
        heldout = {tuple(words(text)) for text, _ in HOLDOUT}
        self.assertFalse(training & heldout)
        self.assertEqual(len(heldout), len(HOLDOUT))
        result = evaluate_model()
        self.assertEqual(result['training_size'], 24)
        self.assertEqual(result['total'], len(HOLDOUT))
        actual = sum(sample['expected'] == sample['predicted'] for sample in result['samples'])
        self.assertEqual(result['correct'], actual)
        self.assertAlmostEqual(result['accuracy'], actual / result['total'])
        self.assertEqual(sum(sum(row.values()) for row in result['confusion'].values()), result['total'])
        for expected, row in result['confusion'].items():
            for predicted, count in row.items():
                self.assertEqual(count, sum(sample['expected'] == expected and sample['predicted'] == predicted
                                            for sample in result['samples']))


if __name__ == '__main__':
    unittest.main()

