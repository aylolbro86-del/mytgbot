"""Админ-команды бота."""

import logging

from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command, BaseFilter
from sqlalchemy import select, func

from config import config
from database import AsyncSessionLocal, get_or_create_user, User
from subscription import activate_plan, get_user_status_text, PLANS

logger = logging.getLogger(__name__)
router = Router()


class IsAdmin(BaseFilter):
    """Фильтр: пропускает только админов. Применяется на уровне роутера."""
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in config.admin_list


# Применяем фильтр ко всему роутеру — не нужно проверять в каждом хендлере
router.message.filter(IsAdmin())


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    text = (
        "🛡 <b>Админ-панель</b>\n\n"
        "/stats — статистика бота\n"
        "/grant &lt;user_id&gt; &lt;plan&gt; — выдать подписку\n"
        "/addreq &lt;user_id&gt; &lt;кол-во&gt; — добавить запросы\n"
        "/userinfo &lt;user_id&gt; — инфо о пользователе\n\n"
        f"<b>Тарифы:</b> {', '.join(PLANS.keys())}"
    )
    await message.answer(text)


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    async with AsyncSessionLocal() as session:
        total_users = (await session.execute(
            select(func.count()).select_from(User)
        )).scalar()
        paid_users = (await session.execute(
            select(func.count()).where(User.paid_requests > 0)
        )).scalar()

    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{total_users}</b>\n"
        f"💎 Платных: <b>{paid_users}</b>"
    )
    await message.answer(text)


@router.message(Command("grant"))
async def cmd_grant(message: Message):
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /grant &lt;user_id&gt; &lt;plan_key&gt;")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("❌ user_id должен быть числом.")
        return

    plan_key = parts[2]
    if plan_key not in PLANS:
        await message.answer(f"❌ Неизвестный план. Доступные: {', '.join(PLANS.keys())}")
        return

    async with AsyncSessionLocal() as session:
        await get_or_create_user(session, target_id)
        await activate_plan(session, target_id, plan_key)

    await message.answer(f"✅ Пользователю {target_id} выдан: <b>{PLANS[plan_key]['name']}</b>")
    logger.warning("Admin %d granted %s to %d", message.from_user.id, plan_key, target_id)


@router.message(Command("addreq"))
async def cmd_addreq(message: Message):
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /addreq &lt;user_id&gt; &lt;количество&gt;")
        return

    try:
        target_id = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        await message.answer("❌ user_id и количество должны быть числами.")
        return

    async with AsyncSessionLocal() as session:
        user = await get_or_create_user(session, target_id)
        user.paid_requests += amount
        await session.commit()

    await message.answer(f"✅ +{amount} запросов для {target_id}.")


@router.message(Command("userinfo"))
async def cmd_userinfo(message: Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Формат: /userinfo &lt;user_id&gt;")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("❌ user_id должен быть числом.")
        return

    async with AsyncSessionLocal() as session:
        user = await session.get(User, target_id)
        if not user:
            await message.answer("❌ Пользователь не найден.")
            return

        unlimited = "нет"
        if user.unlimited_until:
            unlimited = user.unlimited_until.strftime("%d.%m.%Y")

        text = (
            f"👤 <b>User {target_id}</b>\n\n"
            f"Персона: {user.persona}\n"
            f"Платные запросы: {user.paid_requests}\n"
            f"Безлимит до: {unlimited}\n"
            f"Сегодня использовано: {user.daily_used}\n\n"
            f"{get_user_status_text(user)}"
        )
        await message.answer(text)
