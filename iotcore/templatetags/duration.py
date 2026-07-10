from django import template

register = template.Library()


@register.filter
def duration(seconds):
    """
    초(seconds)를 사람이 읽기 쉬운 형태로 변환한다.

    예)
    5      -> 5초
    65     -> 1분 5초
    3600   -> 1시간
    3723   -> 1시간 2분 3초
    """

    if seconds is None:
        return ""

    seconds = int(seconds)

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60

    parts = []

    if hours:
        parts.append(f"{hours}시간")

    if minutes:
        parts.append(f"{minutes}분")

    if seconds:
        parts.append(f"{seconds}초")

    if not parts:
        return "0초"

    return " ".join(parts)