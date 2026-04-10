"""Link FACEIT nickname; FSM when updating existing registration."""

from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import database as dbmod
import referral_state
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
)
from faceit_messages import html_faceit_transport_error
from keyboards.inline import register_confirm_kb, register_success_kb, unlink_confirm_kb, with_navigation
from ui_text import bold, code, italic

router = Router(name="register")

_register_locks: dict[int, asyncio.Lock] = {}


def _register_lock(telegram_id: int) -> asyncio.Lock:
    if telegram_id not in _register_locks:
        _register_locks[telegram_id] = asyncio.Lock()
    return _register_locks[telegram_id]


class RegisterStates(StatesGroup):
    confirm_update = State()


@router.message(Command("register"))
async def cmd_register(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    db,
    faceit,
) -> None:
    tid = message.from_user.id
    async with _register_lock(tid):
        await state.clear()
        if not command.args or not command.args.strip():
            await message.answer(
                f"{bold('Usage')}: {code('/register your_faceit_nickname')}",
                parse_mode=ParseMode.HTML,
                reply_markup=with_navigation(),
            )
            return

        nickname = command.args.strip()

        existing = await dbmod.get_user(db, tid)

        try:
            player = await faceit.get_player_by_nickname(nickname)
        except FaceitNotFoundError:
            await message.answer(
                bold("That nickname was not found on FACEIT.") + "\nDouble-check spelling.",
                parse_mode=ParseMode.HTML,
                reply_markup=with_navigation(),
            )
            return
        except (FaceitUnavailableError, FaceitRateLimitError, FaceitAPIError) as exc:
            await message.answer(
                html_faceit_transport_error(exc),
                parse_mode=ParseMode.HTML,
                reply_markup=with_navigation(),
            )
            return

        pid = player.get("player_id")
        resolved_nick = player.get("nickname") or nickname
        if not pid:
            await message.answer(
                bold("Unexpected FACEIT response."),
                parse_mode=ParseMode.HTML,
                reply_markup=with_navigation(),
            )
            return

        if existing:
            same_account = (
                (existing["faceit_nickname"] or "").lower()
                == (resolved_nick or "").lower()
                and str(existing["faceit_player_id"]) == str(pid)
            )
            if same_account:
                await message.answer(
                    f"{bold('Already linked')}\n"
                    f"You are already connected as {code(resolved_nick)}.\n"
                    f"{italic('Change nickname anytime:')} {code('/register new_nickname')}\n"
                    f"Use the buttons below to open stats or matches.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=register_success_kb(),
                )
                return

            await state.set_state(RegisterStates.confirm_update)
            await state.update_data(
                pending_nickname=resolved_nick,
                pending_player_id=pid,
            )
            await message.answer(
                f"{bold('Change linked account?')}\n"
                f"Current: {code(existing['faceit_nickname'])}\n"
                f"New: {code(resolved_nick)}\n\n"
                f"{bold('Replace your link?')}",
                parse_mode=ParseMode.HTML,
                reply_markup=register_confirm_kb(),
            )
            return

        await dbmod.upsert_user(db, tid, resolved_nick, pid)

        # Credit referrer if this is a brand-new user arriving via a referral link.
        # Validate the referrer is a real registered user before writing anything.
        referrer_id = referral_state.consume_pending(tid)
        referral_note = ""
        if referrer_id:
            referrer_exists = await dbmod.get_user(db, referrer_id)
            already = await dbmod.has_been_referred(db, tid)
            if referrer_exists and not already:
                credited = await dbmod.add_referral(db, referrer_id, tid)
                if credited:
                    referral_note = (
                        f"\n\n👥 {italic('You were referred — your friend just earned a referral point!')}"
                    )

        await message.answer(
            f"{bold('You are set!')}\n"
            f"Linked as {code(resolved_nick)}.\n"
            f"{italic('Change nickname anytime:')} {code('/register new_nickname')}\n"
            f"Try {code('/stats')} or tap ⭐ My stats below."
            + referral_note,
            parse_mode=ParseMode.HTML,
            reply_markup=register_success_kb(),
        )


@router.callback_query(StateFilter(RegisterStates.confirm_update), F.data == "reg:cancel")
async def cb_reg_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.answer(
            bold("Update cancelled."),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
    await callback.answer()


@router.callback_query(StateFilter(RegisterStates.confirm_update), F.data == "reg:confirm")
async def cb_reg_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    db,
) -> None:
    uid = callback.from_user.id
    async with _register_lock(uid):
        data = await state.get_data()
        nick = data.get("pending_nickname")
        pid = data.get("pending_player_id")
        if not nick or not pid:
            await callback.answer("Session expired — run /register again.", show_alert=True)
            return

        await state.clear()
        await dbmod.upsert_user(db, uid, nick, pid)
        if callback.message:
            await callback.message.answer(
                f"{bold('Profile updated')}\nNow linked as {code(nick)}.\n"
                f"{italic('Change nickname anytime:')} {code('/register new_nickname')}",
                parse_mode=ParseMode.HTML,
                reply_markup=register_success_kb(),
            )
        await callback.answer("Saved")


@router.message(Command("unlink"))
async def cmd_unlink(message: Message, db) -> None:
    tid = message.from_user.id
    u = await dbmod.get_user(db, tid)
    if not u:
        await message.answer(
            f"{bold('Nothing to unlink')}\n"
            f"You do not have a linked FACEIT account.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    await message.answer(
        f"{bold('Unlink FACEIT?')}\n"
        f"Current: {code(u['faceit_nickname'])}\n\n"
        f"{bold('This only removes the link in this bot — your FACEIT account is unchanged.')}",
        parse_mode=ParseMode.HTML,
        reply_markup=unlink_confirm_kb(),
    )


@router.callback_query(F.data == "unlink:cancel")
async def cb_unlink_cancel(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(
            bold("Unlink cancelled."),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
    await callback.answer()


@router.callback_query(F.data == "unlink:confirm")
async def cb_unlink_confirm(callback: CallbackQuery, db) -> None:
    ok = await dbmod.delete_user(db, callback.from_user.id)
    if callback.message:
        if ok:
            await callback.message.answer(
                bold("Unlinked.") + "\nUse /register to connect again.",
                parse_mode=ParseMode.HTML,
                reply_markup=with_navigation(),
            )
        else:
            await callback.message.answer(
                bold("No link was stored."),
                parse_mode=ParseMode.HTML,
                reply_markup=with_navigation(),
            )
    await callback.answer("Done" if ok else "OK")
