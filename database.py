from datetime import date

from sqlalchemy import (
    Column, Integer, String, BigInteger, Text, Date, DateTime,
    ForeignKey, Index, text,
)
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.pool import StaticPool

from config import config

engine = create_async_engine(
    config.db_url,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    tg_id = Column(BigInteger, primary_key=True)
    persona = Column(String, default="jarvis")
    paid_requests = Column(Integer, default=0)
    unlimited_until = Column(DateTime, nullable=True)
    daily_used = Column(Integer, default=0)
    daily_date = Column(Date, default=date.today)

    # Реферальная система
    referred_by = Column(BigInteger, nullable=True)  # Кто пригласил
    referral_count = Column(Integer, default=0)  # Сколько пригласил

    history = relationship(
        "MessageHistory", back_populates="user",
        cascade="all, delete-orphan", lazy="noload",
    )


class MessageHistory(Base):
    __tablename__ = "message_history"
    __table_args__ = (Index("ix_msg_user_id", "user_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.tg_id"))
    role = Column(String)
    content = Column(Text)

    user = relationship("User", back_populates="history", lazy="noload")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_user", "chat_id", "user_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(Integer, nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.tg_id"))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))


async def get_or_create_user(session: AsyncSession, tg_id: int) -> User:
    user = await session.get(User, tg_id)
    if not user:
        user = User(tg_id=tg_id)
        session.add(user)
        await session.commit()
    return user
