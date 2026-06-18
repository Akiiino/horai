"""The daemon: wires the template, the store, a scheduler, and the bot together.

One asyncio process. APScheduler fires nudges and nags; python-telegram-bot
delivers them and handles the buttons. All persistent truth lives in the store,
so a restart simply re-reads it and re-schedules.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Protocol, cast

from apscheduler.schedulers.asyncio import (  # pyright: ignore[reportMissingTypeStubs]
    AsyncIOScheduler,
)
from telegram import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import store, template
from .config import Config

log = logging.getLogger("eunomia")


# APScheduler ships no type information, so its API surface comes through as
# ``Unknown`` under strict checking. These Protocols pin down exactly the slice
# we use; the lone ``cast`` in ``Eunomia.__init__`` is the only untyped seam.
class _Job(Protocol):
    def remove(self) -> None: ...


class _Scheduler(Protocol):
    def start(self) -> None: ...
    def add_job(
        self, func: Callable[..., Any], trigger: str, **kwargs: Any
    ) -> _Job: ...
    def get_job(self, job_id: str) -> _Job | None: ...

_STATUS_ICON = {
    store.PENDING: "•",
    store.DONE: "✅",
    store.SKIPPED: "✗",
    store.MISSED: "➖",
}

_DEFAULT_ROUTINE = """\
# Eunomia routine. Edit freely — the daemon hot-reloads this file.
[[block]]
name  = "Morning Routine"
start = "07:30"
end   = "07:50"
days  = "daily"
steps = ["wash face", "brush teeth", "shower"]

[[block]]
name  = "Lunch"
start = "13:00"
end   = "13:45"
days  = "daily"
"""


class Eunomia:
    def __init__(self, config: Config):
        self.cfg = config
        self.chat_id = config.chat_id  # mutable: may be learned from first message
        self.conn = store.connect(config.db_path)
        self.blocks: list[template.Block] = []
        self.routine_mtime: float | None = None
        self.routine_error: bool = False
        self._routine_notice: str | None = None
        self.scheduler: _Scheduler = cast(
            "_Scheduler", AsyncIOScheduler(timezone=config.tz)
        )
        # PTB annotates ``post_init``'s callback param as a bare, unparameterized
        # ``Application``, so its member type comes through as partially unknown.
        builder = Application.builder().token(config.token or "0:dry-run")
        builder = builder.post_init(  # pyright: ignore[reportUnknownMemberType]
            self._on_start
        )
        self.app = builder.build()
        self._register_handlers()

    # ---- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        if self.cfg.dry_run:
            self._run_dry()
        else:
            self.app.run_polling(allowed_updates=Update.ALL_TYPES)

    def _run_dry(self) -> None:
        import asyncio

        async def main():
            await self._on_start(self.app)
            log.info("dry-run: scheduler running, no Telegram polling. Ctrl-C to stop.")
            await asyncio.Event().wait()

        asyncio.run(main())

    async def _on_start(self, _app: object) -> None:
        self.reload_routine(force=True)
        self.scheduler.start()
        self.materialize_and_schedule(self.now().date())
        self.scheduler.add_job(
            self.rollover,
            "cron",
            hour=0,
            minute=1,
            id="rollover",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.watch_routine,
            "interval",
            seconds=30,
            id="watch",
            replace_existing=True,
        )
        if self.cfg.wrapup is not None:
            self.scheduler.add_job(
                self.fire_wrapup,
                "cron",
                hour=self.cfg.wrapup.hour,
                minute=self.cfg.wrapup.minute,
                id="wrapup",
                replace_existing=True,
            )
        if not self.cfg.dry_run:
            await self.app.bot.set_my_commands(
                [
                    BotCommand("now", "What should I be doing right now"),
                    BotCommand("today", "Today's blocks and their status"),
                    BotCommand("routine", "Show the loaded routine"),
                    BotCommand("stats", "Adherence over the last 7 days"),
                    BotCommand("help", "How Eunomia works"),
                ]
            )
        await self._flush_routine_notice()
        log.info("Eunomia started")

    def _register_handlers(self) -> None:
        self.app.add_handler(CommandHandler(["start", "help"], self.cmd_help))
        self.app.add_handler(CommandHandler("now", self.cmd_now))
        self.app.add_handler(CommandHandler("today", self.cmd_today))
        self.app.add_handler(CommandHandler("routine", self.cmd_routine))
        self.app.add_handler(CommandHandler("stats", self.cmd_stats))
        self.app.add_handler(CallbackQueryHandler(self.on_button))
        self.app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(r"(?i)\bwhat now\b"),
                self.cmd_now,
            )
        )

    # ---- helpers -----------------------------------------------------------

    def now(self) -> dt.datetime:
        return dt.datetime.now(self.cfg.tz)

    def block_for(self, name: str) -> template.Block | None:
        return next((b for b in self.blocks if b.name == name), None)

    def _owner(self, update: Update) -> bool:
        if self.chat_id == 0 and update.effective_chat is not None:
            self.chat_id = update.effective_chat.id
            log.info("adopted owner chat id %s", self.chat_id)
        return (
            update.effective_chat is not None
            and update.effective_chat.id == self.chat_id
        )

    async def _send(
        self, text: str, markup: InlineKeyboardMarkup | None = None
    ) -> int | None:
        if self.cfg.dry_run:
            log.info("[dry-run] would send: %s", text)
            return None
        if self.chat_id == 0:
            log.warning("no chat id yet (message me once); dropping: %s", text)
            return None
        msg = await self.app.bot.send_message(self.chat_id, text, reply_markup=markup)
        return msg.message_id

    async def _edit_card(
        self,
        message_id: int,
        text: str,
        markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        if self.cfg.dry_run or self.chat_id == 0:
            log.info("[dry-run] would edit %s: %s", message_id, text)
            return
        try:
            await self.app.bot.edit_message_text(
                text, self.chat_id, message_id, reply_markup=markup
            )
        except BadRequest as exc:  # message gone or unchanged — nothing to do
            log.warning("could not edit message %s: %s", message_id, exc)

    async def _delete_card(self, message_id: int) -> None:
        if self.cfg.dry_run or self.chat_id == 0:
            return
        try:
            await self.app.bot.delete_message(self.chat_id, message_id)
        except BadRequest as exc:  # already gone
            log.warning("could not delete message %s: %s", message_id, exc)

    async def _flush_routine_notice(self) -> None:
        if self._routine_notice is not None:
            notice, self._routine_notice = self._routine_notice, None
            await self._send(notice)

    def _nudge_text(self, inst: sqlite3.Row) -> str:
        hhmm = dt.datetime.fromisoformat(inst["scheduled_start"]).strftime("%H:%M")
        return f"🔔 {hhmm} — {inst['block_name']}"

    def _keyboard(self, iid: int, block: template.Block | None) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if block and block.steps:
            for s in store.steps(self.conn, iid):
                mark = "✅" if s["done"] else "☐"
                rows.append(
                    [
                        InlineKeyboardButton(
                            f"{mark} {s['step_text']}",
                            callback_data=f"step:{iid}:{s['position']}",
                        )
                    ]
                )
        rows.append(
            [
                InlineKeyboardButton("✓ Done", callback_data=f"done:{iid}"),
                InlineKeyboardButton("✗ Skip", callback_data=f"skip:{iid}"),
                InlineKeyboardButton("+15m", callback_data=f"snooze:{iid}"),
            ]
        )
        return InlineKeyboardMarkup(rows)

    def _cancel(self, iid: int) -> None:
        for jid in (f"nudge:{iid}", f"nag:{iid}"):
            job = self.scheduler.get_job(jid)
            if job:
                job.remove()

    def _terminal_line(self, inst: sqlite3.Row) -> str:
        name = inst["block_name"]
        status = inst["status"]
        if status == store.DONE:
            return f"✅ {name} — done"
        if status == store.SKIPPED:
            return f"✗ {name} — skipped"
        if status == store.MISSED:
            return f"➖ Marked “{name}” as missed. No worries."
        return self._nudge_text(inst)

    def _terminal_markup(self, iid: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩ Undo", callback_data=f"undo:{iid}")]]
        )

    # ---- routine loading ---------------------------------------------------

    def _seed_default(self) -> None:
        os.makedirs(os.path.dirname(self.cfg.routine_path) or ".", exist_ok=True)
        with open(self.cfg.routine_path, "w") as f:
            f.write(_DEFAULT_ROUTINE)
        log.info("seeded default routine at %s", self.cfg.routine_path)

    def reload_routine(self, *, force: bool = False) -> None:
        try:
            mtime = os.path.getmtime(self.cfg.routine_path)
        except FileNotFoundError:
            self._seed_default()
            mtime = os.path.getmtime(self.cfg.routine_path)
        if not force and mtime == self.routine_mtime:
            return
        try:
            self.blocks = template.load_blocks(self.cfg.routine_path)
            self.routine_mtime = mtime
            log.info(
                "loaded %d blocks from %s", len(self.blocks), self.cfg.routine_path
            )
            if self.routine_error:  # recovered from a previous bad edit
                self.routine_error = False
                self._routine_notice = "✅ Routine reloaded."
        except Exception as exc:  # keep the last good routine on a bad edit
            log.error("could not load routine %s: %s", self.cfg.routine_path, exc)
            if not self.routine_error:  # announce the breakage once, not every tick
                self.routine_error = True
                self._routine_notice = f"⚠️ Couldn't parse routine.toml: {exc}"

    # These two are scheduler callbacks. They must be async: APScheduler's
    # AsyncIOExecutor runs plain functions in a worker thread, but the SQLite
    # connection is bound to the loop thread (check_same_thread), so a sync job
    # touching the store would raise. Coroutines run on the loop thread.
    async def watch_routine(self) -> None:
        before = self.routine_mtime
        self.reload_routine()
        await self._flush_routine_notice()
        if self.routine_mtime != before:
            self.materialize_and_schedule(self.now().date())

    async def rollover(self) -> None:
        """At the start of a new day: roll yesterday up and lay out today."""
        self.reload_routine()
        await self._flush_routine_notice()
        self.materialize_and_schedule(self.now().date())

    # ---- scheduling --------------------------------------------------------

    def materialize_and_schedule(self, date: dt.date) -> None:
        store.mark_missed_before(self.conn, date, now=self.now())
        for block in self.blocks:
            if not template.active_on(block, date):
                continue
            start = template.start_at(block, date, self.cfg.tz)
            iid, _ = store.upsert_instance(self.conn, block, date, start)
            inst = store.get_instance(self.conn, iid)
            assert inst is not None  # just upserted
            if inst["status"] != store.PENDING:
                continue
            if self.scheduler.get_job(f"nudge:{iid}") or self.scheduler.get_job(
                f"nag:{iid}"
            ):
                continue
            when = dt.datetime.fromisoformat(inst["scheduled_start"])
            self._schedule_nudge(iid, when, block)

    def _schedule_nudge(
        self, iid: int, when: dt.datetime, block: template.Block | None
    ) -> None:
        now = self.now()
        if block is not None and now >= template.end_at(
            block, when.date(), self.cfg.tz
        ):
            return  # whole window already passed; leave pending, rolled over later
        run_at = when if when > now else now + timedelta(seconds=5)
        self.scheduler.add_job(
            self.fire_nudge,
            "date",
            run_date=run_at,
            args=[iid],
            id=f"nudge:{iid}",
            replace_existing=True,
        )

    def _schedule_nag(self, iid: int, block: template.Block | None) -> None:
        every = block.nag_every_min if block else 10
        self.scheduler.add_job(
            self.fire_nag,
            "date",
            run_date=self.now() + timedelta(minutes=every),
            args=[iid],
            id=f"nag:{iid}",
            replace_existing=True,
        )

    async def fire_nudge(self, iid: int) -> None:
        inst = store.get_instance(self.conn, iid)
        if inst is None or inst["status"] != store.PENDING:
            return
        block = self.block_for(inst["block_name"])
        mid = await self._send(self._nudge_text(inst), self._keyboard(iid, block))
        store.set_message_id(self.conn, iid, mid)
        self._schedule_nag(iid, block)

    async def fire_nag(self, iid: int) -> None:
        inst = store.get_instance(self.conn, iid)
        if inst is None or inst["status"] != store.PENDING:
            return
        block = self.block_for(inst["block_name"])
        nag_max = block.nag_max if block else 3
        count = store.bump_nag(self.conn, iid)
        if count > nag_max:
            store.set_status(self.conn, iid, store.MISSED, now=self.now())
            done = store.get_instance(self.conn, iid)
            assert done is not None  # just set
            await self._land_card(iid, inst["message_id"], self._terminal_line(done))
            return
        # Still pending: drop the old card and repost at the bottom, so the nag
        # resurfaces (and re-notifies) instead of piling a new message onto it.
        if inst["message_id"] is not None:
            await self._delete_card(inst["message_id"])
        hhmm = dt.datetime.fromisoformat(inst["scheduled_start"]).strftime("%H:%M")
        text = f"⏰ {hhmm} — {inst['block_name']}  (×{count})"
        mid = await self._send(text, self._keyboard(iid, block))
        store.set_message_id(self.conn, iid, mid)
        self._schedule_nag(iid, block)

    async def _land_card(
        self, iid: int, message_id: int | None, text: str
    ) -> None:
        """Settle an instance's live card to a terminal line with an Undo button."""
        markup = self._terminal_markup(iid)
        if message_id is not None:
            await self._edit_card(message_id, text, markup)
        else:
            mid = await self._send(text, markup)
            store.set_message_id(self.conn, iid, mid)

    # ---- bot handlers ------------------------------------------------------

    async def on_button(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        assert query is not None and query.data is not None  # CallbackQueryHandler
        if not self._owner(update):
            await query.answer()
            return
        action, _, rest = query.data.partition(":")
        iid = int(rest.split(":")[0])
        inst = store.get_instance(self.conn, iid)
        if inst is None:
            await query.answer()
            await query.edit_message_text("(this block is gone)")
            return
        name = inst["block_name"]

        # A stale card (e.g. an earlier nag that's since been resolved elsewhere):
        # don't re-apply the action — just collapse it to its real terminal state.
        if action != "undo" and inst["status"] in store.TERMINAL:
            await query.answer(f"already {inst['status']}")
            await query.edit_message_text(
                self._terminal_line(inst), reply_markup=self._terminal_markup(iid)
            )
            return

        await query.answer()
        if action == "step":
            position = int(rest.split(":")[1])
            store.toggle_step(self.conn, iid, position)
            await query.edit_message_reply_markup(
                self._keyboard(iid, self.block_for(name))
            )
        elif action == "done":
            store.set_status(self.conn, iid, store.DONE, now=self.now())
            self._cancel(iid)
            await self._collapse(query, iid)
        elif action == "skip":
            store.set_status(self.conn, iid, store.SKIPPED, now=self.now())
            self._cancel(iid)
            await self._collapse(query, iid)
        elif action == "snooze":
            new = self.now() + timedelta(minutes=15)
            store.reschedule(self.conn, iid, new)
            store.set_message_id(self.conn, iid, None)  # the fresh nudge opens a new card
            self._cancel(iid)
            self._schedule_nudge(iid, new, self.block_for(name))
            await query.edit_message_text(
                f"⏰ {name} — snoozed to {new.strftime('%H:%M')}"
            )
        elif action == "undo":
            store.set_status(self.conn, iid, store.PENDING)
            reopened = store.get_instance(self.conn, iid)
            assert reopened is not None  # just reopened
            await query.edit_message_text(
                self._nudge_text(reopened),
                reply_markup=self._keyboard(iid, self.block_for(name)),
            )

    async def _collapse(self, query: CallbackQuery, iid: int) -> None:
        """Edit the just-resolved card down to one terminal line plus Undo."""
        inst = store.get_instance(self.conn, iid)
        assert inst is not None
        await query.edit_message_text(
            self._terminal_line(inst), reply_markup=self._terminal_markup(iid)
        )

    async def cmd_now(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._owner(update):
            return
        assert update.message is not None
        now = self.now()
        block = template.current_block(self.blocks, now)
        if block is None:
            nxt = template.next_block(self.blocks, now)
            msg = (
                f"Nothing scheduled right now. Next: {nxt.name} at {nxt.start:%H:%M}."
                if nxt
                else "Nothing left on the schedule today."
            )
            await update.message.reply_text(msg)
            return
        inst = store.find_instance(self.conn, block.name, now.date())
        lines = [f"{now:%H:%M} — you're in “{block.name}”."]
        markup = None
        if inst is not None:
            top = store.first_undone_step(self.conn, inst["id"])
            if top:
                lines.append(f"Top item: {top}")
            markup = self._keyboard(inst["id"], block)
        await update.message.reply_text("\n".join(lines), reply_markup=markup)

    async def cmd_today(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._owner(update):
            return
        assert update.message is not None
        rows = store.instances_on(self.conn, self.now().date())
        if not rows:
            await update.message.reply_text("Nothing scheduled today.")
            return
        lines: list[str] = []
        for r in rows:
            hhmm = dt.datetime.fromisoformat(r["scheduled_start"]).strftime("%H:%M")
            lines.append(
                f"{hhmm} {_STATUS_ICON.get(r['status'], '?')} {r['block_name']}"
            )
        await update.message.reply_text("\n".join(lines))

    async def cmd_routine(
        self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._owner(update):
            return
        assert update.message is not None
        if not self.blocks:
            await update.message.reply_text("No routine loaded.")
            return
        lines: list[str] = []
        for b in sorted(self.blocks, key=lambda b: b.start):
            days = template.format_days(b.days)
            steps = f" · {len(b.steps)} steps" if b.steps else ""
            lines.append(
                f"{b.start:%H:%M}–{b.end:%H:%M}  {b.name}  ({days}){steps}"
            )
        await update.message.reply_text("\n".join(lines))

    async def cmd_stats(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._owner(update):
            return
        assert update.message is not None
        since = self.now().date() - timedelta(days=7)
        rows = store.adherence(self.conn, since)
        if not rows:
            await update.message.reply_text(
                "No history yet — check back in a few days."
            )
            return
        lines = ["Last 7 days:"]
        for r in rows:
            total = r["total"]
            pct = round(100 * r["done"] / total) if total else 0
            extra: list[str] = []
            if r["skipped"]:
                extra.append(f"{r['skipped']} skipped")
            if r["missed"]:
                extra.append(f"{r['missed']} missed")
            tail = f" · {' · '.join(extra)}" if extra else ""
            lines.append(f"{r['block_name']}: {r['done']}/{total} ({pct}%){tail}")
        await update.message.reply_text("\n".join(lines))

    async def fire_wrapup(self) -> None:
        rows = store.instances_on(self.conn, self.now().date())
        if not rows:
            return
        done = sum(r["status"] == store.DONE for r in rows)
        skipped = sum(r["status"] == store.SKIPPED for r in rows)
        missed = sum(r["status"] == store.MISSED for r in rows)
        total = len(rows)
        parts = [f"Today: {done}/{total} done"]
        if skipped:
            parts.append(f"{skipped} skipped")
        if missed:
            parts.append(f"{missed} missed")
        suffix = " 🎉" if done == total else ""
        await self._send(" · ".join(parts) + suffix)

    async def cmd_help(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._owner(update):
            return
        assert update.message is not None
        await update.message.reply_text(
            "I keep your routine.\n"
            "/now — what should I be doing right now\n"
            "/today — today's blocks and their status\n"
            "/routine — show the loaded routine\n"
            "/stats — adherence over the last 7 days\n"
            "Buttons on each nudge: Done · Skip · +15m."
        )
