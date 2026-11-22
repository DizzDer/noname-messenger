"""Жеделдік моделін оқыту және салмақталған бағаларды есептеу."""
from collections import Counter
from collections.abc import Mapping
from math import exp, isfinite, log
from numbers import Real
import re

TRAIN = {
    'Жедел': [
        'шұғыл көмек керек', 'дереу жауап берші', 'тез хабарлас',
        'мерзім бүгін аяқталады', 'қазір тез жібер', 'жүйе істемей тұр шұғыл',
        'дереу қоңырау шал', 'жедел шешім керек', 'тез тексер қате шықты',
        'қазір көмектесші', 'шұғыл жауап керек', 'бүгін мерзім бітеді',
    ],
    'Қалыпты': [
        'ертең кездесеміз бе', 'бос кезде қарай сал', 'сәлем қалайсың',
        'апта соңында талқылайық', 'кейін хабарласамын', 'рахмет жақсы',
        'келесі аптада жібер', 'асықпай қарап шық', 'ертең хабарлас',
        'жай ғана сұрайын дедім', 'кейін оқып көр', 'ертең жоспар құрамыз',
    ],
}

# Бұл сөйлемдер модельді оқытуға қосылмайды. Олар тек шағын, бекітілген
# бақылау жиыны; осы нәтижеден барлық қазақша мәтіннің сапасы шықпайды.
HOLDOUT = (
    ('Тез жібер, бүгін мерзім бітеді', 'Жедел'),
    ('Келесі аптада кездесеміз бе', 'Қалыпты'),
    ('Жүйеде қате шықты, шұғыл тексер', 'Жедел'),
    ('Кейін қарап шық, асықпай', 'Қалыпты'),
    ('Дереу хабарлас, көмек керек', 'Жедел'),
    ('Сәлем, ертең сөйлесеміз', 'Қалыпты'),
    ('Бүгін аяқталады, тез тексеріп жібер', 'Жедел'),
    ('Жедел көмек керек, жүйе ашылмайды', 'Жедел'),
    ('Қазір қоңырау шал, қате пайда болды', 'Жедел'),
    ('Мерзім бітеді, дереу шешім қабылдайық', 'Жедел'),
    ('Шұғыл емес, ертең қарасаң болады', 'Қалыпты'),
    ('Бос кезде апта жоспарын талқылайық', 'Қалыпты'),
    ('Рахмет, келесі аптада хабарласамын', 'Қалыпты'),
    ('Асықпай оқып шық, кейін сұраймын', 'Қалыпты'),
    ('Көмек керек, дереу жауап күтемін', 'Жедел'),
    ('Жай ғана сәлем берейін дедім', 'Қалыпты'),
)

DEFAULT_WEIGHTS = {'urgency': .65, 'wait': .25, 'recent': .10}


def validate_weights(weights=None):
    """Үш критерий салмағын тексеріп, қосындысын бірге келтіру.

    Салмақтарды 0.65/0.25/0.10 не 65/25/10 түрінде беруге болады.
    """
    if weights is None:
        return DEFAULT_WEIGHTS.copy()
    if not isinstance(weights, Mapping) or set(weights) != set(DEFAULT_WEIGHTS):
        raise ValueError('Жеделдік, күту және жаңалық салмақтарын көрсетіңіз.')
    values = {}
    for key in DEFAULT_WEIGHTS:
        value = weights[key]
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError('Салмақ сан болуы керек.')
        try:
            value = float(value)
        except (ValueError, OverflowError):
            raise ValueError('Салмақ шекті сан болуы керек.') from None
        if not isfinite(value) or value < 0:
            raise ValueError('Салмақ нөлден кем емес шекті сан болуы керек.')
        values[key] = value
    scale = max(values.values())
    if scale == 0:
        raise ValueError('Кемінде бір салмақ нөлден үлкен болуы керек.')
    # Алдымен ең үлкен санға бөлу қосындының асып кетуіне жол бермейді.
    scaled = {key: value / scale for key, value in values.items()}
    total = sum(scaled.values())
    return {key: value / total for key, value in scaled.items()}


def words(value):
    return re.findall(r'[^\W\d_]+', value.casefold())


class UrgencyModel:
    """Лаплас тегістеуі бар мультиномиалды аңғал Байес моделі."""
    def __init__(self):
        self.counts = {label: Counter(w for s in rows for w in words(s))
                       for label, rows in TRAIN.items()}
        self.vocab = set().union(*(set(c) for c in self.counts.values()))

    def predict(self, message):
        seen = [word for word in words(message) if word in self.vocab]
        def weight(label, word):
            counts = self.counts[label]
            return log((counts[word] + 1) / (counts.total() + len(self.vocab)))
        signals = {w: weight('Жедел', w) - weight('Қалыпты', w) for w in set(seen)}
        z = max(-60, min(60, sum(signals[w] for w in seen)))
        return 1 / (1 + exp(-z)), sorted(signals, key=lambda w: (-abs(signals[w]), w))[:4]


MODEL = UrgencyModel()


def evaluate_model():
    """Оқуға кірмеген бекітілген сөйлемдер бойынша ашық тексеру нәтижесі."""
    labels = tuple(TRAIN)
    confusion = {expected: {predicted: 0 for predicted in labels} for expected in labels}
    samples = []
    correct = 0
    for text, expected in HOLDOUT:
        probability, _ = MODEL.predict(text)
        predicted = 'Жедел' if probability >= .5 else 'Қалыпты'
        correct += predicted == expected
        confusion[expected][predicted] += 1
        samples.append({'text': text, 'expected': expected, 'predicted': predicted,
                        'probability': probability})
    return {'accuracy': correct / len(samples), 'correct': correct, 'total': len(samples),
            'confusion': confusion, 'samples': samples,
            'training_size': sum(map(len, TRAIN.values()))}


def demo_messages(now):
    """Үш баламаны салыстыруға арналған айқын белгіленген оқу мысалы."""
    return [
        {'id': 1, 'sender': 2, 'recipient': 1, 'body': 'Шұғыл көмек керек, дереу хабарлас',
         'sent': now - 15 * 60},
        {'id': 2, 'sender': 2, 'recipient': 1, 'body': 'Ертең жоспар құрамыз, асықпай қарап шық',
         'sent': now - 20 * 3600},
        {'id': 3, 'sender': 2, 'recipient': 1, 'body': 'Қазір тез тексер, мерзім бүгін аяқталады',
         'sent': now - 3 * 3600},
    ]


def rank(messages, my_id, now, weights=None):
    """Кіріс хабарларды салмақталған қосынды әдісімен реттеу.

    Барлық критерийлер 0–1 аралығында және үлкен мәні жоғары басымдық
    білдіреді. Модель ықтималдығы эмпирикалық калибрленбеген оқу бағасы.
    """
    normalized = validate_weights(weights)
    rows = []
    for message in messages:
        if message['sender'] == my_id:
            continue
        p, signals = MODEL.predict(message['body'])
        age = max(0, (now - message['sent']) / 3600)
        wait = min(age / 24, 1)
        recent = 1 if age <= 1 else 0
        score = 100 * (normalized['urgency'] * p + normalized['wait'] * wait
                       + normalized['recent'] * recent)
        rows.append((score, p, wait, recent, signals, message))
    return sorted(rows, key=lambda row: (-row[0], -row[5]['id']))

