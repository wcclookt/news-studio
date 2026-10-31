"""Recipient addresses control delivery; profiles contain display metadata only."""

from copy import deepcopy
import re

RECIPIENT_FIELDS = {
    'qq': ('private_qq', 'private_qq_profiles', 'user_id', 'nickname'),
    'email': ('to', 'to_profiles', 'address', 'name'),
    'telegram': ('chat_ids', 'chat_profiles', 'chat_id', 'title'),
}


def normalize_ids(value, numeric=False):
    values = re.split(r'[,，;；\s]+', value) if isinstance(value, str) else value
    if type(values) is int:
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    for item in values:
        if type(item) not in (str, int):
            continue
        text = str(item).strip()
        if text and (not numeric or text.isascii() and text.isdigit()) and text not in result:
            result.append(text)
    return result


def normalize_profiles(value, key, numeric=False):
    if not isinstance(value, list):
        return []
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            continue
        ids = normalize_ids([item.get(key)], numeric=numeric)
        if not ids or ids[0] in seen:
            continue
        seen.add(ids[0])
        profile = {key: ids[0]}
        for field in ('nickname', 'name', 'title', 'note'):
            if isinstance(item.get(field), str):
                profile[field] = item[field].strip()
        result.append(profile)
    return result


def ids_from_profiles(profiles, key, numeric=False):
    return [item[key] for item in normalize_profiles(profiles, key, numeric)]


def recipient_ids(config, channel):
    ids_key, profiles_key, key, _ = RECIPIENT_FIELDS[channel]
    if ids_key in config:
        return normalize_ids(config[ids_key], numeric=channel == 'qq')
    if channel == 'telegram' and 'chat_id' in config:
        return normalize_ids(config['chat_id'])
    # Profile-only imports work, but an explicit empty address list always wins.
    return ids_from_profiles(config.get(profiles_key, []), key, numeric=channel == 'qq')


def recipient_rows(config, channel):
    _, profiles_key, key, name_key = RECIPIENT_FIELDS[channel]
    profiles = {item[key]: item for item in normalize_profiles(config.get(profiles_key, []), key)}
    return [{key: identifier, name_key: profiles.get(identifier, {}).get(name_key, ''),
             'note': profiles.get(identifier, {}).get('note', '')}
            for identifier in recipient_ids(config, channel)]


def merge_profile(profiles, key, identifier, **fields):
    current = normalize_profiles(profiles, key)
    identifier = str(identifier).strip()
    profile = next((item for item in current if item[key] == identifier), None)
    if profile is None:
        profile = {key: identifier}
        current.append(profile)
    for name, value in fields.items():
        if isinstance(value, str) and value.strip():
            # Only the operator changes a custom note once it has been written.
            if name != 'note' or not profile.get('note'):
                profile[name] = value.strip()
    return current


def remove_profile(profiles, key, identifier):
    return [item for item in normalize_profiles(profiles, key) if item[key] != str(identifier)]


def merge_refreshed_rows(original, edited, latest, key):
    """Keep local field edits over a refreshed snapshot without reviving removals."""
    before = {row[key]: row for row in original}
    local = {row[key]: row for row in edited}
    result = []
    for incoming in latest:
        identifier = incoming[key]
        if identifier in before and identifier not in local:
            continue
        row = deepcopy(incoming)
        for field, value in local.get(identifier, {}).items():
            if field != key and value != before.get(identifier, {}).get(field, ''):
                row[field] = value
        result.append(row)
    incoming_ids = {row[key] for row in latest}
    result.extend(deepcopy(row) for row in edited if row[key] not in before and row[key] not in incoming_ids)
    return result
