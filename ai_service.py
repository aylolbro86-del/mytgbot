import logging
import asyncio

import openai
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from database import MessageHistory, get_or_create_user
from prompts import PERSONAS

logger = logging.getLogger(__name__)

client = openai.AsyncOpenAI(
    api_key=config.openai_api_key,
    base_url=config.ai_base_url,
    timeout=float(config.ai_timeout),
    max_retries=1,
)

_ai_semaphore = asyncio.Semaphore(config.ai_max_concurrent)


async def generate_reply(session: AsyncSession, tg_id: int, text: str) -> str:
    """Генерация ответа от ИИ с учётом истории диалога."""
    user = await get_or_create_user(session, tg_id)

    text = text[: config.max_message_length]

    # Сохраняем сообщение пользователя
    session.add(MessageHistory(user_id=tg_id, role="user", content=text))
    await session.flush()  # flush вместо commit — одна транзакция

    # История (только нужные поля)
    stmt = (
        select(MessageHistory.role, MessageHistory.content)
        .where(MessageHistory.user_id == tg_id)
        .order_by(MessageHistory.id.desc())
        .limit(config.memory_window)
    )
    rows = (await session.execute(stmt)).all()[::-1]

    # Промпт
    persona_data = PERSONAS.get(user.persona, PERSONAS["jarvis"])
    messages = [{"role": "system", "content": persona_data["prompt"]}]
    messages.extend({"role": r, "content": c} for r, c in rows)

    # Запрос к AI
    async with _ai_semaphore:
        try:
            response = await client.chat.completions.create(
                model=config.ai_model,
                messages=messages,
                temperature=config.ai_temperature,
                max_tokens=config.ai_max_tokens,
            )
        except openai.APITimeoutError:
            await session.commit()
            return "⏳ ИИ думает слишком долго. Попробуй ещё раз."
        except openai.RateLimitError:
            await session.commit()
            return "🚫 Слишком много запросов. Подожди немного."
        except openai.APIError:
            await session.commit()
            return "❌ Ошибка сервиса ИИ. Попробуй позже."
        except Exception:
            logger.exception("Ошибка AI для user %d", tg_id)
            await session.commit()
            return "❌ Произошла ошибка. Попробуй позже."

    reply_text = response.choices[0].message.content
    if not reply_text:
        await session.commit()
        return "🤖 ИИ не смог ответить. Переформулируй вопрос."

    # Сохраняем ответ
    session.add(MessageHistory(user_id=tg_id, role="assistant", content=reply_text))
    await session.commit()

    # Автоочистка старой истории (раз в N сообщений, не каждый раз)
    await _maybe_trim_history(session, tg_id)

    return reply_text


async def _maybe_trim_history(session: AsyncSession, tg_id: int):
    """Удаляет старые записи, если их слишком много."""
    max_keep = config.memory_window * 3
    total = (await session.execute(
        select(func.count()).where(MessageHistory.user_id == tg_id)
    )).scalar()

    if total <= max_keep:
        return

    old_ids = (await session.execute(
        select(MessageHistory.id)
        .where(MessageHistory.user_id == tg_id)
        .order_by(MessageHistory.id.asc())
        .limit(total - max_keep)
    )).scalars().all()

    if old_ids:
        await session.execute(
            delete(MessageHistory).where(MessageHistory.id.in_(old_ids))
        )
        await session.commit()


async def clear_user_memory(session: AsyncSession, tg_id: int):
    """Очистка истории сообщений пользователя."""
    await session.execute(
        delete(MessageHistory).where(MessageHistory.user_id == tg_id)
    )
    await session.commit()
