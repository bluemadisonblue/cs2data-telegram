"""Link FACEIT nickname; FSM when updating existing registration."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import database as dbmod
from faceit_api import (
    FaceitAPIError,
    FaceitNotFoundError,
    FaceitRateLimitError,
    FaceitUnavailableError,
)
from keyboards.inline import register_confirm_kb, with_navigation
from ui_text import bold, code

router = Router(name="register")


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
    await state.clear()
    if not command.args or not command.args.strip():
        await message.answer(
            f"{bold('Usage')}: {code('/register your_faceit_nickname')}",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return

    nickname = command.args.strip()
    tid = message.from_user.id

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
    except FaceitUnavailableError:
        await message.answer(
            bold("FACEIT is temporarily unavailable."),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except FaceitRateLimitError:
        await message.answer(
            bold("FACEIT rate limit — try again in a minute."),
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
        return
    except FaceitAPIError:
        await message.answer(
            bold("Could not reach FACEIT."),
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
                f"Use the buttons below to open stats or matches.",
                parse_mode=ParseMode.HTML,
                reply_markup=with_navigation(),
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
    await message.answer(
        f"{bold('You are set!')}\n"
        f"Linked as {code(resolved_nick)}.\n"
        f"Try {code('/stats')} or the buttons below.",
        parse_mode=ParseMode.HTML,
        reply_markup=with_navigation(),
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
    data = await state.get_data()
    await state.clear()
    nick = data.get("pending_nickname")
    pid = data.get("pending_player_id")
    if not nick or not pid:
        await callback.answer("Session expired — run /register again.", show_alert=True)
        return

    await dbmod.upsert_user(db, callback.from_user.id, nick, pid)
    if callback.message:
        await callback.message.answer(
            f"{bold('Profile updated')}\nNow linked as {code(nick)}.",
            parse_mode=ParseMode.HTML,
            reply_markup=with_navigation(),
        )
    await callback.answer("Saved")
