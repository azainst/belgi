"""Telegram-бот для жителей (русский и казахский): статус заявления, инструкции, «Сообщить о нарушении»."""
import asyncio
import html
import logging
import re
import uuid

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           Message, ReplyKeyboardMarkup)

from . import db, services
from .i18n import APP_STATUS_KK, KNOWLEDGE, PARCEL_STATUS, all_langs, t

log = logging.getLogger("bot")
router = Router()
bot: Bot | None = None

TRACK_RE = re.compile(r"\bKZ-\d{4}-\d{3,}\b", re.IGNORECASE)          # трек-номер заявления в акимат
SIGNAL_RE = re.compile(r"\bNK-\d{3,}\b", re.IGNORECASE)               # номер обращения о нарушении
ANY_TRACK_RE = re.compile(TRACK_RE.pattern + "|" + SIGNAL_RE.pattern, re.IGNORECASE)
STATUS_ICON = {"На рассмотрении": "🕓", "Назначен выезд инспектора": "🚗", "Одобрено": "✅", "Отказ": "⛔"}
PARCEL_ICON = {"ok": "⚪", "signal": "🟡", "violation": "🔴", "in_progress": "🟠", "resolved": "🟢"}

# Пока ждём номер после «Проверить статус заявления», не перехватываем нажатия других кнопок меню.
MENU_TEXTS = (all_langs("menu_track") | all_langs("menu_kb") | all_langs("menu_signal")
             | all_langs("menu_mine") | all_langs("menu_lang") | all_langs("cancel"))


class TrackFlow(StatesGroup):
    waiting = State()


class SignalFlow(StatesGroup):
    location = State()
    photo = State()
    text = State()


def lang_of(chat_id: int) -> str:
    return services.get_lang(chat_id) or "ru"


# ---------- клавиатуры ----------

def main_kb(lang: str) -> ReplyKeyboardMarkup:
    b = lambda k: KeyboardButton(text=t(lang, k))
    return ReplyKeyboardMarkup(
        keyboard=[[b("menu_track")], [b("menu_kb")], [b("menu_signal")], [b("menu_mine"), b("menu_lang")]],
        resize_keyboard=True,
    )


def cancel_kb(lang: str, extra: list[KeyboardButton] | None = None) -> ReplyKeyboardMarkup:
    rows = ([extra] if extra else []) + [[KeyboardButton(text=t(lang, "cancel"))]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
        InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="lang:kk"),
    ]])


def kb_inline(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=k["title"], callback_data=f"kb:{i}")]
                         for i, k in enumerate(KNOWLEDGE[lang])]
    )


# ---------- старт, язык, отмена ----------

@router.message(CommandStart())
@router.message(Command("menu"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    if services.get_lang(message.chat.id) is None:
        await message.answer(t("ru", "choose_lang"), reply_markup=lang_kb())
        return
    lang = lang_of(message.chat.id)
    await message.answer(t(lang, "welcome"), reply_markup=main_kb(lang))


@router.message(F.text.in_(all_langs("menu_lang")))
@router.message(Command("lang"))
async def change_lang(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(t("ru", "choose_lang"), reply_markup=lang_kb())


@router.callback_query(F.data.startswith("lang:"))
async def set_lang(cb: CallbackQuery):
    lang = cb.data.split(":")[1]
    services.set_lang(cb.message.chat.id, lang)
    await cb.message.answer(t(lang, "welcome"), reply_markup=main_kb(lang))
    await cb.answer(t(lang, "lang_set"))


@router.message(F.text.in_(all_langs("cancel")))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    lang = lang_of(message.chat.id)
    await message.answer(t(lang, "cancelled"), reply_markup=main_kb(lang))


# ---------- статус: заявление в акимат ИЛИ обращение о нарушении ----------

@router.message(F.text.in_(all_langs("menu_track")))
async def ask_track(message: Message, state: FSMContext):
    lang = lang_of(message.chat.id)
    await state.set_state(TrackFlow.waiting)
    await message.answer(t(lang, "ask_track"), reply_markup=cancel_kb(lang))


async def send_app_status(message: Message, track: str) -> bool:
    lang = lang_of(message.chat.id)
    app = services.get_application(track)
    if not app:
        return False
    kk = lang == "kk"
    subject = (app.get("subject_kk") if kk else None) or app["subject"]
    expl = (app.get("explanation_kk") if kk else None) or app["explanation"]
    status = APP_STATUS_KK.get(app["status"], app["status"]) if kk else app["status"]
    await message.answer(
        t(lang, "app_card", track=app["track"], subject=html.escape(subject), icon=STATUS_ICON.get(app["status"], "•"),
          status=status, expl=html.escape(expl), date=app["updated_at"][:10]),
        reply_markup=main_kb(lang),
    )
    return True


async def send_signal_status(message: Message, code: str) -> bool:
    lang = lang_of(message.chat.id)
    sid = services.parse_signal_code(code)
    info = services.get_signal_status(sid) if sid else None
    if not info:
        return False
    extra = t(lang, "signal_deadline", deadline=info["deadline"]) if info["deadline"] and info["status"] in ("violation", "in_progress") else ""
    await message.answer(
        t(lang, "signal_card", code=info["code"], date=info["created_at"][:10], icon=PARCEL_ICON[info["status"]],
          status=PARCEL_STATUS[info["status"]][lang], extra=extra),
        reply_markup=main_kb(lang),
    )
    return True


async def resolve_track(message: Message, raw: str) -> bool:
    """Заявление (KZ-...) или обращение о нарушении (NK-...) — пробуем оба варианта."""
    lang = lang_of(message.chat.id)
    m = TRACK_RE.search(raw)
    if m and await send_app_status(message, m.group(0)):
        return True
    m = SIGNAL_RE.search(raw)
    if m and await send_signal_status(message, m.group(0)):
        return True
    await message.answer(t(lang, "not_found", text=html.escape(raw.strip()[:40])), reply_markup=cancel_kb(lang))
    return False


@router.message(TrackFlow.waiting, F.text, ~F.text.in_(MENU_TEXTS))
async def got_track(message: Message, state: FSMContext):
    if await resolve_track(message, message.text):
        await state.clear()


# ---------- база знаний ----------

@router.message(F.text.in_(all_langs("menu_kb")))
async def kb_menu(message: Message, state: FSMContext):
    await state.clear()
    lang = lang_of(message.chat.id)
    await message.answer(t(lang, "kb_pick"), reply_markup=kb_inline(lang))


@router.callback_query(F.data.startswith("kb:") & (F.data != "kb:back"))
async def kb_show(cb: CallbackQuery):
    lang = lang_of(cb.message.chat.id)
    item = KNOWLEDGE[lang][int(cb.data.split(":")[1])]
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(item["steps"], 1))
    docs = "\n".join(f"• {d}" for d in item["docs"])
    text = (f"<b>{item['title']}</b>\n\n<b>{t(lang, 'kb_steps')}</b>\n{steps}\n\n<b>{t(lang, 'kb_docs')}</b>\n{docs}\n\n"
            f"<b>{t(lang, 'kb_term')}:</b> {item['term']}\n\n<i>{t(lang, 'kb_note')}</i>")
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "kb_open", name=item["link"][0]), url=item["link"][1])],
        [InlineKeyboardButton(text=t(lang, "kb_back"), callback_data="kb:back")],
    ])
    await cb.message.answer(text, reply_markup=markup, disable_web_page_preview=True)
    await cb.answer()


@router.callback_query(F.data == "kb:back")
async def kb_back(cb: CallbackQuery):
    lang = lang_of(cb.message.chat.id)
    await cb.message.answer(t(lang, "kb_pick"), reply_markup=kb_inline(lang))
    await cb.answer()


# ---------- Сообщить о нарушении ----------

@router.message(F.text.in_(all_langs("menu_signal")))
async def signal_start(message: Message, state: FSMContext):
    lang = lang_of(message.chat.id)
    await state.clear()
    await state.set_state(SignalFlow.location)
    await message.answer(t(lang, "sig_1"),
                         reply_markup=cancel_kb(lang, [KeyboardButton(text=t(lang, "send_geo"), request_location=True)]))


@router.message(SignalFlow.location, F.location)
async def signal_location(message: Message, state: FSMContext):
    lang = lang_of(message.chat.id)
    await state.update_data(lat=message.location.latitude, lon=message.location.longitude)
    await state.set_state(SignalFlow.photo)
    await message.answer(t(lang, "sig_2"), reply_markup=cancel_kb(lang, [KeyboardButton(text=t(lang, "skip"))]))


@router.message(SignalFlow.location)
async def signal_location_wrong(message: Message):
    await message.answer(t(lang_of(message.chat.id), "sig_1_wrong"))


@router.message(SignalFlow.photo, F.photo)
async def signal_photo(message: Message, state: FSMContext):
    """Фото можно прислать несколько (в том числе альбомом) — каждое добавляется в список,
    пока житель не нажмёт «Готово»."""
    lang = lang_of(message.chat.id)
    name = f"{uuid.uuid4().hex}.jpg"
    await message.bot.download(message.photo[-1], destination=db.UPLOAD_DIR / name)
    data = await state.get_data()
    photos = data.get("photos", [])
    photos.append(name)
    await state.update_data(photos=photos)
    await message.answer(t(lang, "photo_added", n=len(photos)),
                         reply_markup=cancel_kb(lang, [KeyboardButton(text=t(lang, "photo_done"))]))


@router.message(SignalFlow.photo, F.text.in_(all_langs("photo_done")))
async def signal_photo_done(message: Message, state: FSMContext):
    lang = lang_of(message.chat.id)
    data = await state.get_data()
    if not data.get("photos"):
        await message.answer(t(lang, "photo_need_one"))
        return
    await state.set_state(SignalFlow.text)
    await message.answer(t(lang, "sig_3"), reply_markup=cancel_kb(lang, [KeyboardButton(text=t(lang, "skip"))]))


@router.message(SignalFlow.photo, F.text.in_(all_langs("skip")))
async def signal_photo_skip(message: Message, state: FSMContext):
    """Фото можно пропустить, но тогда следующий шаг — описание словами — станет обязательным."""
    lang = lang_of(message.chat.id)
    data = await state.get_data()
    await state.set_state(SignalFlow.text)
    if data.get("photos"):
        await message.answer(t(lang, "sig_3"), reply_markup=cancel_kb(lang, [KeyboardButton(text=t(lang, "skip"))]))
    else:
        await message.answer(t(lang, "sig_3_required"), reply_markup=cancel_kb(lang))


@router.message(SignalFlow.photo)
async def signal_photo_wrong(message: Message):
    await message.answer(t(lang_of(message.chat.id), "sig_2_wrong"))


@router.message(SignalFlow.text, F.text)
async def signal_text(message: Message, state: FSMContext):
    lang = lang_of(message.chat.id)
    data = await state.get_data()
    has_photos = bool(data.get("photos"))
    if message.text in all_langs("skip"):
        if not has_photos:
            await message.answer(t(lang, "sig_3_need_text"))
            return
        text = ""
    else:
        text = message.text
    res = services.create_signal(
        lat=data["lat"], lon=data["lon"], text=text, photos=data.get("photos"),
        chat_id=message.chat.id, username=message.from_user.username if message.from_user else None,
    )
    await state.clear()
    place = t(lang, "place_merged" if res["merged"] else "place_known" if res["matched"] else "place_new")
    await message.answer(
        t(lang, "sig_done", code=res["code"], place=place, track_btn=t(lang, "menu_track")),
        reply_markup=main_kb(lang),
    )


@router.message(SignalFlow.text)
async def signal_text_wrong(message: Message):
    await message.answer(t(lang_of(message.chat.id), "sig_3_wrong"))


# ---------- мои обращения ----------

def signal_card_text(lang: str, info: dict) -> str:
    extra = (t(lang, "signal_deadline", deadline=info["deadline"])
            if info["deadline"] and info["status"] in ("violation", "in_progress") else "")
    return t(lang, "signal_card", code=info["code"], date=info["created_at"][:10], icon=PARCEL_ICON[info["status"]],
            status=PARCEL_STATUS[info["status"]][lang], extra=extra)


@router.message(F.text.in_(all_langs("menu_mine")))
async def my_signals(message: Message, state: FSMContext):
    await state.clear()
    lang = lang_of(message.chat.id)
    rows = services.my_signals(message.chat.id)
    if not rows:
        await message.answer(t(lang, "mine_empty"), reply_markup=main_kb(lang))
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=f"{PARCEL_ICON[r['status']]} {services.signal_code(r['id'])} — {PARCEL_STATUS[r['status']][lang]} ({r['created_at'][:10]})",
        callback_data=f"mine:{r['id']}",
    )] for r in rows])
    await message.answer(t(lang, "mine_title"), reply_markup=kb)


@router.callback_query(F.data.startswith("mine:"))
async def mine_detail(cb: CallbackQuery):
    lang = lang_of(cb.message.chat.id)
    sid = int(cb.data.split(":")[1])
    info = services.get_my_signal_detail(cb.message.chat.id, sid)
    if not info:
        await cb.answer(t(lang, "mine_gone"), show_alert=True)
        return
    await cb.answer()
    await cb.bot.send_location(cb.message.chat.id, latitude=info["lat"], longitude=info["lon"])
    for ph in info["photos"]:
        path = db.UPLOAD_DIR / ph["filename"]
        if path.exists():
            await cb.bot.send_photo(cb.message.chat.id, FSInputFile(path), caption=ph["caption"] or None)
    await cb.message.answer(signal_card_text(lang, info))


# ---------- прочее ----------

@router.message(StateFilter(None), F.text.regexp(ANY_TRACK_RE, search=True))
async def track_anywhere(message: Message):
    """Номер, присланный без нажатия кнопки, тоже понимаем."""
    if services.get_lang(message.chat.id) is None:
        services.set_lang(message.chat.id, "ru")
    m = ANY_TRACK_RE.search(message.text)
    await resolve_track(message, m.group(0))


@router.message()
async def fallback(message: Message):
    if services.get_lang(message.chat.id) is None:
        await message.answer(t("ru", "choose_lang"), reply_markup=lang_kb())
        return
    lang = lang_of(message.chat.id)
    await message.answer(t(lang, "fallback"), reply_markup=main_kb(lang))


# ---------- запуск ----------

async def notify(chat_id: int, text: str) -> None:
    if bot is None:
        return
    try:
        await bot.send_message(chat_id, text)
    except Exception:
        log.exception("Не удалось отправить уведомление %s", chat_id)


async def run_bot(token: str) -> None:
    global bot
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    try:
        me = await bot.get_me()
        log.warning("Telegram-бот запущен: @%s", me.username)
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, handle_signals=False)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Бот остановлен из-за ошибки (проверьте BOT_TOKEN и интернет). Веб-панель продолжает работать.")
    finally:
        await bot.session.close()
        bot = None
