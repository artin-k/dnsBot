def format_money(amount: int | None) -> str:
    return f"{amount or 0:,}"


def format_toman(amount: int | None) -> str:
    return format_money(amount)
