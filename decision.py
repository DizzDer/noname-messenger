"""Жеделдік моделін оқыту және салмақталған бағаларды есептеу."""
from collections import Counter
from math import exp, log
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


def rank(messages, my_id, now):
    rows = []
    for message in messages:
        if message['sender'] == my_id:
            continue
        p, signals = MODEL.predict(message['body'])
        age = max(0, (now - message['sent']) / 3600)
        wait = min(age / 24, 1)
        recent = 1 if age <= 1 else 0
        score = 100 * (.65 * p + .25 * wait + .10 * recent)
        rows.append((score, p, wait, recent, signals, message))
    return sorted(rows, key=lambda row: (-row[0], -row[5]['id']))

