from secrets import token_hex


def generate_discount_code() -> str:
    return f"DICE-{token_hex(3).upper()}"
