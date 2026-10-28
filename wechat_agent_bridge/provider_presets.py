"""Explicit provider selection; presets never contain credentials or grants."""
from .console_models import ConsoleError

DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
DEEPSEEK_MODEL = 'deepseek-flash'


def preset(name):
    if name != 'deepseek':
        raise ConsoleError('unknown_provider_preset')
    return dict(provider='compatible', base_url=DEEPSEEK_BASE_URL,
                model=DEEPSEEK_MODEL, vision_model='', transcription_model='')


def is_deepseek(config):
    return config.get('base_url', '').rstrip('/') == DEEPSEEK_BASE_URL
