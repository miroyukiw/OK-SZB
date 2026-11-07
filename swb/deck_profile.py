"""Deck profile routing for the Rotation Discard/Tempo Dragon deck."""
import re
from pathlib import Path
from swb.atomic_io import read_json

DEFAULT_PROFILE = 'dragon_tempo'
PROFILES = {
    'dragon_tempo': '疾驰弃牌龙（ドラゴン）',
    'needle_dragon': '疾驰弃牌龙（ドラゴン）',
    'nightmare_aggro': '疾驰弃牌龙（ドラゴン）',
}

DECK_NAME_PATTERN = r'ドラゴン|[龍龙]族|疾[馳驰][龍龙]|棄牌[龍龙]|弃牌[龍龙]|跳[費费][龍龙]|速攻速攻'


def resolve_deck_profile(value=None):
    return DEFAULT_PROFILE


def is_nightmare(value):
    return False


def deck_name_matches(text):
    s = re.sub(r'\s+', '', str(text or ''))
    return bool(s and re.search(DECK_NAME_PATTERN, s, re.I))


def active(root, config=None):
    return DEFAULT_PROFILE


def asset_root(root, profile=None):
    return Path(root) / 'assets'
