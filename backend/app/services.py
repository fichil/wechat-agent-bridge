"""Convert native values without mutating the account probe's public contract."""


def browser_values(value):
    if isinstance(value, dict):
        return {key: str(item) if key == "created" and isinstance(item, int)
                else browser_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [browser_values(item) for item in value]
    return value
