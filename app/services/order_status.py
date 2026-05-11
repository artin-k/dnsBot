from app.utils.formatting import format_order_status_fa, format_order_type_fa


def order_status_label(status: str) -> str:
    return format_order_status_fa(status)


def order_kind_label(kind: str | None) -> str:
    return format_order_type_fa(kind)
