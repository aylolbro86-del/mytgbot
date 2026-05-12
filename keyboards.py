from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from prompts import PERSONAS
from subscription import PLANS


def get_main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎭 Личности", callback_data="change_persona")
    builder.button(text="💎 Подписка", callback_data="subscription")
    builder.button(text="🧹 Очистить чат", callback_data="clear_memory")
    builder.adjust(2, 1)
    return builder.as_markup()


def get_personas_keyboard(current_persona: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, data in PERSONAS.items():
        marker = "● " if key == current_persona else "○ "
        label = "🆓" if key == "jarvis" else "💎"
        builder.button(
            text=f"{marker}{label} {data['name']}",
            callback_data=f"set_persona_{key}",
        )
    builder.button(text="◀️ Назад", callback_data="back_to_menu")
    builder.adjust(2)
    return builder.as_markup()


def get_subscription_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, plan in PLANS.items():
        emoji = "♾" if plan["unlimited"] else "📦"
        builder.button(
            text=f"{emoji} {plan['name']} — {plan['stars']}⭐",
            callback_data=f"buy_{key}",
        )
    builder.button(text="◀️ Назад", callback_data="back_to_menu")
    builder.adjust(1)
    return builder.as_markup()
