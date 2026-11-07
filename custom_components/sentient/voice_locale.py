"""Small, explicit speech/answer catalogues. Unknown wording never grants consent."""
import re
import unicodedata


def clean(text):
    text = unicodedata.normalize('NFKC', text or '').casefold().replace('’', "'")
    return ' '.join(re.sub(r"[^\w\s']", ' ', text).split())


ANSWERS = {
    'fi': {'yes': {'kyllä', 'kyllä kiitos', 'joo'}, 'no': {'ei', 'ei kiitos', 'älä', 'peruuta'},
           'end': {'ei kiitos', 'siinä kaikki', 'riittää', 'lopeta kuuntelu', 'peruuta'}},
    'fr': {'yes': {'oui', "oui s'il vous plaît", 'oui merci'}, 'no': {'non', 'non merci', 'annule', 'annuler'},
           'end': {'non merci', "c'est tout", 'ça suffit', "arrête d'écouter", 'annule', 'annuler'}},
}


def answer(text, language):
    catalog = ANSWERS.get((language or '').split('-')[0].split('_')[0].lower(), {})
    said = clean(text)
    return next((kind for kind in ('yes', 'no') if said in catalog.get(kind, set())), None)


def ending(text, language):
    return clean(text) in ANSWERS.get((language or '').split('-')[0].split('_')[0].lower(), {}).get('end', set())


def cancel(text, language=None):
    from .sessions import END

    said = re.sub(r'\bthankyou\b', 'thank you', clean(text))
    return bool(END.fullmatch(said)) or said in {'cancel', 'cancel that', 'cancel it', 'leave it', 'leave that',
                           'actually leave it', 'actually cancel that', 'never mind', 'nevermind'} or ending(text, language)


# A colon keeps arbitrary room/device names intact without guessing their grammar.
ALERTS = {
    'fi': {'Smoke detected': 'Savua havaittu', 'Carbon monoxide detected': 'Häkää havaittu',
           'Gas detected': 'Kaasua havaittu', 'Water leak detected': 'Vesivuoto havaittu',
           'Safety alarm': 'Turvahälytys', 'Tamper alarm': 'Peukalointihälytys', 'Alert': 'Hälytys',
           'Smoke': 'Savua havaittu', 'Alarm': 'Hälytys'},
    'fr': {'Smoke detected': 'Fumée détectée', 'Carbon monoxide detected': 'Monoxyde de carbone détecté',
           'Gas detected': 'Gaz détecté', 'Water leak detected': "Fuite d'eau détectée",
           'Safety alarm': 'Alarme de sécurité', 'Tamper alarm': 'Alarme de sabotage', 'Alert': 'Alerte',
           'Smoke': 'Fumée détectée', 'Alarm': 'Alarme'},
}


def safety_sentence(text, language):
    catalog = ALERTS.get(language.lower().replace('_', '-').split('-')[0], {})
    for original, translated in catalog.items():
        match = re.fullmatch(re.escape(original) + r'(?: in the |: )(.+?)[.!]*', text)
        if match:
            return f'{translated}: {match[1]}!'
        if text.rstrip('.!') == original:
            return translated + '!'
    match = re.fullmatch(r'The (.+) has gone off!', text)
    if match and catalog:
        return f"{catalog['Alarm']}: {match[1]}!"
    return None

FOLLOWUPS = {'fi': 'Voinko auttaa vielä jossakin?', 'fr': 'Puis-je vous aider avec autre chose ?'}


def generic_question(text, language):
    code = (language or '').split('-')[0].lower()
    value = clean(text)
    return (code == 'fi' and ('vielä jossakin' in value or 'jotain muuta' in value)) or (code == 'fr' and 'autre chose' in value)
