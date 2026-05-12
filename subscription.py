"""Управление подпиской и лимитами запросов."""

from datetime import datetime, date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from database import User, get_or_create_user

PLANS = {
    "plan_10": {
        "name": "10 запросов",
        "stars": 15,
        "requests": 10,
        "unlimited": False,
        "description": "10 запросов к ИИ",
    },
    "plan_25": {
        "name": "25 запросов",
        "stars": 50,
        "requests": 25,
        "unlimited": False,
        "description": "25 запросов — скидка 25%",
    },
    "plan_100": {
        "name": "100 запросов",
        "stars": 150,
        "requests": 100,
        "unlimited": False,
        "description": "100 запросов — скидка 40%",
    },
    "plan_300": {
        "name": "300 запросов",
        "stars": 250,
        "requests": 300,
        "unlimited": False,
        "description": "300 запросов — скидка 55%",
    },
}


def has_paid_access(user: User) -> bool:
    """Есть ли у пользователя платный доступ."""
    if user.unlimited_until and user.unlimited_until > datetime.utcnow():
        return True
    return user.paid_requests > 0


def _reset_daily_if_needed(user: User) -> None:
    """Сбрасывает дневной счётчик если наступил новый день."""
    today = date.today()
    if user.daily_date != today:
        user.daily_used = 0
        user.daily_date = today


# Бесплатные персоны (с дневным лимитом)
FREE_PERSONAS = {"jarvis", "rude", "cute"}


async def check_can_send(session: AsyncSession, user: User) -> tuple[bool, str]:
    """
    Проверяет, может ли пользователь отправить запрос.
    Принимает уже загруженного user (без повторного запроса к БД).
    """
    if has_paid_access(user):
        return True, ""

    if user.persona not in FREE_PERSONAS:
        return False, (
            "🔒 Эта персона доступна только с подпиской.\n\n"
            "Купи пакет запросов, чтобы общаться\n"
            "с премиум-персонами 👇"
        )

    # Бесплатные персоны — дневной лимит
    _reset_daily_if_needed(user)

    if user.daily_used < config.free_daily_limit:
        return True, ""

    return False, (
        f"⚠️ Ты использовал все {config.free_daily_limit} бесплатных сообщений на сегодня.\n\n"
        "Купи пакет запросов, чтобы продолжить 👇"
    )


async def consume_request(session: AsyncSession, user: User):
    """Списывает один запрос. Принимает уже загруженного user."""
    if user.unlimited_until and user.unlimited_until > datetime.utcnow():
        return

    if user.paid_requests > 0:
        user.paid_requests -= 1
        await session.commit()
        return

    if user.persona in FREE_PERSONAS:
        user.daily_used += 1
        await session.commit()


async def activate_plan(session: AsyncSession, tg_id: int, plan_key: str):
    """Активирует тарифный план после оплаты."""
    user = await get_or_create_user(session, tg_id)
    plan = PLANS[plan_key]

    if plan["unlimited"]:
        now = datetime.utcnow()
        base = user.unlimited_until if (user.unlimited_until and user.unlimited_until > now) else now
        user.unlimited_until = base + timedelta(days=30)
    else:
        user.paid_requests += plan["requests"]

    await session.commit()


def get_user_status_text(user: User) -> str:
    """Формирует текст статуса (синхронно, без запросов к БД)."""
    lines = []

    if user.unlimited_until and user.unlimited_until > datetime.utcnow():
        days_left = (user.unlimited_until - datetime.utcnow()).days
        lines.append(f"  ♾  Безлимит: <b>активен</b> ({days_left} дн.)")
    else:
        lines.append("  ♾  Безлимит: <i>не активен</i>")

    paid = user.paid_requests
    bar = "█" * min(paid // 10, 10) + "░" * max(0, 10 - min(paid // 10, 10))
    lines.append(f"  💎 Запросы: <b>{paid}</b>  [{bar}]")

    _reset_daily_if_needed(user)
    remaining = max(0, config.free_daily_limit - user.daily_used)
    free_bar = "█" * remaining + "░" * (config.free_daily_limit - remaining)
    lines.append(f"  🆓 Jarvis: <b>{remaining}</b>/{config.free_daily_limit}  [{free_bar}]")

    return "\n".join(lines)
