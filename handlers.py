import logging
import time
import asyncio

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery
from aiogram.filters import Command
from aiogram.utils.chat_action import ChatActionSender
from sqlalchemy import select, delete, func

from database import AsyncSessionLocal, get_or_create_user, ChatMessage
from keyboards import get_main_menu, get_personas_keyboard, get_subscription_keyboard
from ai_service import generate_reply, clear_user_memory
from subscription import (
    PLANS, check_can_send, consume_request, activate_plan,
    get_user_status_text, has_paid_access,
)
from config import config
from prompts import PERSONAS

logger = logging.getLogger(__name__)
router = Router()

# Антиспам: LRU-подобный словарь с автоочисткой
_rate_limit: dict[int, float] = {}
_RATE_LIMIT_CLEANUP_THRESHOLD = 10000


def _check_rate_limit(user_id: int) -> bool:
    """Возвращает True если пользователь может отправить сообщение."""
    now = time.monotonic()

    # Периодическая очистка старых записей
    if len(_rate_limit) > _RATE_LIMIT_CLEANUP_THRESHOLD:
        cutoff = now - 60
        to_delete = [k for k, v in _rate_limit.items() if v < cutoff]
        for k in to_delete:
            del _rate_limit[k]

    last = _rate_limit.get(user_id, 0)
    if now - last < config.rate_limit_seconds:
        return False
    _rate_limit[user_id] = now
    return True


async def _track_message(session, chat_id: int, message_id: int, user_id: int):
    """Сохраняет message_id. Чистит старые записи при превышении лимита."""
    session.add(ChatMessage(chat_id=chat_id, message_id=message_id, user_id=user_id))
    await session.flush()

    # Проверяем лимит (раз в 10 сообщений — по остатку от деления id)
    if message_id % 10 != 0:
        return

    total = (await session.execute(
        select(func.count()).where(
            ChatMessage.chat_id == chat_id, ChatMessage.user_id == user_id
        )
    )).scalar()

    if total > config.max_tracked_messages:
        old_ids = (await session.execute(
            select(ChatMessage.id)
            .where(ChatMessage.chat_id == chat_id, ChatMessage.user_id == user_id)
            .order_by(ChatMessage.id.asc())
            .limit(total - config.max_tracked_messages)
        )).scalars().all()
        if old_ids:
            await session.execute(delete(ChatMessage).where(ChatMessage.id.in_(old_ids)))


# ─── Команды ────────────────────────────────────────────────────────────────

@router.message(Command("start", "settings", "menu"))
async def cmd_start(message: Message):
    async with AsyncSessionLocal() as session:
        user = await get_or_create_user(session, message.from_user.id)
        await _track_message(session, message.chat.id, message.message_id, message.from_user.id)
        await session.commit()

    name = message.from_user.first_name or "друг"
    persona_name = PERSONAS.get(user.persona, PERSONAS["jarvis"])["name"]

    text = (
        f"┌─────────────────────────┐\n"
        f"   🤖 <b>AI Assistant Bot</b>\n"
        f"└─────────────────────────┘\n\n"
        f"Привет, <b>{name}</b>! 👋\n\n"
        f"Я ИИ-бот с разными личностями.\n"
        f"Запоминаю контекст беседы.\n\n"
        f"┌ 📌 <b>Текущий режим:</b>\n"
        f"└ {persona_name}\n\n"
        f"<i>Напиши мне или выбери действие:</i>"
    )
    await message.answer(text, reply_markup=get_main_menu())


# ─── Подписка ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "subscription")
async def cb_subscription(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        status = get_user_status_text(user)

    text = (
        f"┌─────────────────────────┐\n"
        f"   💎 <b>Подписка и баланс</b>\n"
        f"└─────────────────────────┘\n\n"
        f"{status}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🛒 <b>Тарифы:</b>\n\n"
    )
    for plan in PLANS.values():
        text += f"  ▸ <b>{plan['name']}</b> — {plan['stars']}⭐\n"
        text += f"    <i>{plan['description']}</i>\n\n"

    await callback.message.edit_text(text, reply_markup=get_subscription_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("buy_"))
async def cb_buy_plan(callback: CallbackQuery):
    plan_key = callback.data.replace("buy_", "")
    if plan_key not in PLANS:
        await callback.answer("Тариф устарел. Обнови меню: /menu", show_alert=True)
        return

    plan = PLANS[plan_key]
    await callback.message.answer_invoice(
        title=plan["name"],
        description=plan["description"],
        payload=plan_key,
        currency="XTR",
        prices=[LabeledPrice(label=plan["name"], amount=plan["stars"])],
    )
    await callback.answer()


@router.pre_checkout_query()
async def on_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    plan_key = message.successful_payment.invoice_payload
    if plan_key not in PLANS:
        await message.answer("❌ Ошибка: неизвестный тариф.")
        return

    async with AsyncSessionLocal() as session:
        await activate_plan(session, message.from_user.id, plan_key)
        user = await get_or_create_user(session, message.from_user.id)
        status = get_user_status_text(user)

    plan = PLANS[plan_key]
    text = (
        f"✅ Оплата прошла!\n"
        f"Активирован: <b>{plan['name']}</b>\n\n"
        f"{status}"
    )
    await message.answer(text, reply_markup=get_main_menu())


# ─── Персоны ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "change_persona")
async def cb_change_persona(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await get_or_create_user(session, callback.from_user.id)

    text = (
        f"┌─────────────────────────┐\n"
        f"   🎭 <b>Выбор личности</b>\n"
        f"└─────────────────────────┘\n\n"
        f"🆓 — бесплатно (5/день)\n"
        f"💎 — нужна подписка\n\n"
        f"Выбери персону:"
    )
    await callback.message.edit_text(text, reply_markup=get_personas_keyboard(user.persona))
    await callback.answer()


@router.callback_query(F.data.startswith("set_persona_"))
async def cb_set_persona(callback: CallbackQuery):
    persona_key = callback.data.replace("set_persona_", "")
    if persona_key not in PERSONAS:
        await callback.answer("❌ Неизвестная личность", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        user = await get_or_create_user(session, callback.from_user.id)

        # Платные персоны — только с подпиской
        from subscription import FREE_PERSONAS
        if persona_key not in FREE_PERSONAS and not has_paid_access(user):
            text = (
                f"🔒 <b>Нужна подписка</b>\n\n"
                f"Персона <b>{PERSONAS[persona_key]['name']}</b>\n"
                f"доступна только с подпиской.\n\n"
                f"💡 Jarvis — 5 сообщений/день бесплатно."
            )
            await callback.message.edit_text(text, reply_markup=get_subscription_keyboard())
            await callback.answer()
            return

        user.persona = persona_key
        await session.commit()
        await clear_user_memory(session, callback.from_user.id)

    persona_name = PERSONAS[persona_key]["name"]
    label = "🆓" if persona_key == "jarvis" else "💎"
    text = f"✅ Режим: <b>{persona_name}</b> {label}\n🧹 Контекст сброшен."
    await callback.message.edit_text(text, reply_markup=get_main_menu())
    await callback.answer()


# ─── Очистка памяти ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "clear_memory")
async def cb_clear_memory(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    bot = callback.bot

    async with AsyncSessionLocal() as session:
        await clear_user_memory(session, user_id)

        # Получаем ID сообщений для удаления
        msg_ids = (await session.execute(
            select(ChatMessage.message_id).where(
                ChatMessage.chat_id == chat_id,
                ChatMessage.user_id == user_id,
            )
        )).scalars().all()

        # Удаляем из Telegram (пачками с задержкой для rate limit)
        for i, msg_id in enumerate(msg_ids):
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass
            # Задержка каждые 20 сообщений (Telegram rate limit ~30/сек)
            if i > 0 and i % 20 == 0:
                await asyncio.sleep(1)

        # Чистим из БД
        await session.execute(
            delete(ChatMessage).where(
                ChatMessage.chat_id == chat_id,
                ChatMessage.user_id == user_id,
            )
        )
        await session.commit()

    await callback.answer("🧹 Очищено!", show_alert=True)


@router.callback_query(F.data == "back_to_menu")
async def cb_back(callback: CallbackQuery):
    await callback.message.edit_text(
        "⚙️ <b>Меню</b>\n\nВыбери действие:", reply_markup=get_main_menu()
    )
    await callback.answer()


# ─── Текстовые сообщения (ИИ) ──────────────────────────────────────────────

@router.message(F.text)
async def handle_text(message: Message):
    """Обработчик текстовых сообщений — общение с ИИ."""
    if message.text.startswith("/"):
        return

    user_id = message.from_user.id

    if not _check_rate_limit(user_id):
        return

    async with AsyncSessionLocal() as session:
        user = await get_or_create_user(session, user_id)

        # Проверяем лимит
        can_send, reason = await check_can_send(session, user)
        if not can_send:
            await session.commit()
            await message.answer(
                f"⚠️ <b>Лимит</b>\n\n{reason}",
                reply_markup=get_subscription_keyboard(),
            )
            return

        await _track_message(session, message.chat.id, message.message_id, user_id)

        # Генерация ответа (в той же сессии)
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            reply = await generate_reply(session, user_id, message.text)

        sent = await message.answer(reply)

        # Списываем запрос и трекаем ответ
        await consume_request(session, user)
        await _track_message(session, sent.chat.id, sent.message_id, user_id)
        await session.commit()
