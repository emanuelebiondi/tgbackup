"""
===============================================================================
Project      : TGBackup
File         : tgbackup/cluster.py
Description  : Telegram bot cluster manager, topic provisioning, and transfer engine.
Purpose      : Implements a pool of Telegram bots with round-robin rotation and
               concurrent parallel multi-worker uploads, forum topic provisioning,
               HTML status notifications, cloud integrity checks (scrubbing),
               and disaster recovery using pinned encrypted catalogs.
===============================================================================
"""

import os
import sys
import glob
import shutil
import asyncio
import logging
import html
from itertools import cycle
from typing import List, Optional, Tuple, Dict, Any, Callable

try:
    from telegram import Bot
    from telegram.error import RetryAfter, TelegramError, BadRequest
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

logger = logging.getLogger("tgbackup.cluster")


def format_size(size_bytes: int) -> str:
    """
    ---------------------------------------------------------------------------
    Function: format_size
    Description:
        Converts a byte count into a human-readable string (B, KB, MB, GB).
    Input parameters:
        @param size_bytes (int) : Byte count to format.
    Return value:
        @return (str)           : Formatted string (e.g. '34.50 MB').
    ---------------------------------------------------------------------------
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_duration(seconds: float) -> str:
    """
    ---------------------------------------------------------------------------
    Function: format_duration
    Description:
        Converts seconds into hours, minutes, and seconds representation.
    Input parameters:
        @param seconds (float) : Duration in seconds.
    Return value:
        @return (str)          : Formatted string (e.g. '1h 12m 45s').
    ---------------------------------------------------------------------------
    """
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}s"
    elif m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


class BotCluster:
    """
    ---------------------------------------------------------------------------
    Class: BotCluster
    Description:
        Coordinates a pool of Telegram bots for parallel multi-worker uploads,
        forum topic routing, status reporting, integrity verification, and
        disaster recovery.
    ---------------------------------------------------------------------------
    """

    def __init__(
        self,
        tokens: List[str],
        channel_id: str,
        use_topics: bool = True,
        api_endpoint: Optional[str] = None,
        rate_limit_per_minute: int = 20,
        mock: bool = False
    ):
        self.mock = mock
        self.use_topics = use_topics
        self.api_endpoint = api_endpoint
        self.rate_limit_per_minute = rate_limit_per_minute
        self._message_timestamps: List[float] = []
        self._rate_lock = asyncio.Lock()
        self.tokens = [t.strip() for t in tokens if t and t.strip()]
        self.channel_id = int(channel_id) if channel_id and not mock else channel_id
        self.mock_msg_counter = int(asyncio.get_event_loop().time() * 1000) if mock else 1000
        self.mock_topic_counter = 100
        self.mock_cloud_dir = os.path.expanduser("~/.local/share/tgbackup/mock_cloud")

        if self.mock:
            os.makedirs(self.mock_cloud_dir, exist_ok=True)
            self.bots = [f"mock_bot_{i}" for i in range(max(1, len(self.tokens)))]
            self._pool = cycle(enumerate(self.bots))
        else:
            if not TELEGRAM_AVAILABLE:
                raise RuntimeError("Library python-telegram-bot not found.")
            if not self.tokens:
                raise ValueError("No Telegram bot tokens configured.")
            if not self.channel_id:
                raise ValueError("No channel/supergroup ID specified.")
            
            if api_endpoint:
                base_url = f"{api_endpoint.rstrip('/')}/bot"
                self.bots = [Bot(token=t, base_url=base_url) for t in self.tokens]
            else:
                self.bots = [Bot(token=t) for t in self.tokens]
            self._pool = cycle(enumerate(self.bots))

    async def _pace_requests(self):
        """
        Enforces rate_limit_per_minute across the bot cluster to avoid Telegram flood limits.
        """
        if self.mock or self.rate_limit_per_minute <= 0:
            return
        async with self._rate_lock:
            now = asyncio.get_event_loop().time()
            self._message_timestamps = [t for t in self._message_timestamps if now - t < 60.0]
            if len(self._message_timestamps) >= self.rate_limit_per_minute:
                oldest = self._message_timestamps[0]
                sleep_needed = 60.0 - (now - oldest) + 0.1
                if sleep_needed > 0:
                    logger.info(f"Rate limit reached ({self.rate_limit_per_minute}/min). Pacing requests: sleeping {sleep_needed:.2f}s...")
                    await asyncio.sleep(sleep_needed)
                    now = asyncio.get_event_loop().time()
                    self._message_timestamps = [t for t in self._message_timestamps if now - t < 60.0]
            self._message_timestamps.append(asyncio.get_event_loop().time())

    def get_next_bot(self) -> Tuple[int, Any]:
        return next(self._pool)

    def get_primary_bot(self) -> Any:
        return self.bots[0]

    async def verify_bots(self) -> List[Dict[str, Any]]:
        results = []
        if self.mock:
            for idx, b in enumerate(self.bots):
                results.append({"index": idx + 1, "valid": True, "username": f"mock_bot_{idx}"})
            return results

        for idx, bot in enumerate(self.bots):
            try:
                me = await bot.get_me()
                results.append({
                    "index": idx + 1,
                    "valid": True,
                    "username": me.username,
                    "first_name": me.first_name,
                    "id": me.id
                })
            except Exception as e:
                results.append({
                    "index": idx + 1,
                    "valid": False,
                    "error": str(e)
                })
        return results

    async def get_or_create_topic(self, topic_name: str, color_hex: int = 0x6FB9F0) -> Optional[int]:
        if not self.use_topics:
            return None

        if self.mock:
            self.mock_topic_counter += 1
            return self.mock_topic_counter

        bot = self.get_primary_bot()
        try:
            topic = await bot.create_forum_topic(
                chat_id=self.channel_id,
                name=topic_name,
                icon_color=color_hex
            )
            logger.info(f"Created Topic '{topic_name}' with thread_id: {topic.message_thread_id}")
            return topic.message_thread_id
        except Exception as e:
            logger.warning(f"Could not create topic '{topic_name}' ({e}). Falling back to general topic.")
            return None

    async def send_notification(self, text: str, thread_id: Optional[int] = None) -> Optional[int]:
        if self.mock:
            self.mock_msg_counter += 1
            return self.mock_msg_counter

        bot = self.get_primary_bot()
        try:
            kwargs = {"chat_id": self.channel_id, "text": text, "parse_mode": "HTML"}
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            msg = await bot.send_message(**kwargs)
            return msg.message_id
        except Exception as e:
            logger.error(f"Error sending notification: {e}")
            return None

    async def edit_notification(self, message_id: Optional[int], new_text: str, thread_id: Optional[int] = None):
        if not message_id:
            await self.send_notification(new_text, thread_id=thread_id)
            return

        if self.mock:
            return

        bot = self.get_primary_bot()
        try:
            await bot.edit_message_text(
                chat_id=self.channel_id,
                message_id=message_id,
                text=new_text,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Notification message #{message_id} not editable ({e}). Sending new...")
            await self.send_notification(new_text, thread_id=thread_id)

    async def upload_part_with_bot(
        self,
        bot_idx: int,
        bot: Any,
        filepath: str,
        profile_name: str,
        thread_id: Optional[int] = None,
        retries: int = 5
    ) -> Tuple[int, int]:
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        if self.mock:
            self.mock_msg_counter += 1
            msg_id = self.mock_msg_counter
            dest_cloud = os.path.join(self.mock_cloud_dir, f"{msg_id}_{filename}")
            shutil.copy(filepath, dest_cloud)
            await asyncio.sleep(0.02)
            return msg_id, bot_idx

        safe_fname = html.escape(filename)
        safe_profile = html.escape(profile_name)
        tag_profile = profile_name.replace(" ", "_")

        for attempt in range(1, retries + 1):
            caption = (
                f"[TGBackup] Snapshot Chunk\n"
                f"Profile: <code>{safe_profile}</code>\n"
                f"File: <code>{safe_fname}</code>\n"
                f"Size: <b>{format_size(filesize)}</b>\n"
                f"Bot: <code>#{bot_idx + 1}</code>\n"
                f"#{tag_profile} #tgbackup"
            )

            try:
                with open(filepath, "rb") as f:
                    kwargs = {
                        "chat_id": self.channel_id,
                        "document": f,
                        "filename": filename,
                        "caption": caption,
                        "parse_mode": "HTML",
                        "read_timeout": 300,
                        "write_timeout": 300,
                        "connect_timeout": 60
                    }
                    if thread_id:
                        kwargs["message_thread_id"] = thread_id

                    await self._pace_requests()
                    msg = await bot.send_document(**kwargs)
                    return msg.message_id, bot_idx

            except RetryAfter as ra:
                logger.warning(f"Telegram Rate Limit on bot #{bot_idx + 1}: sleeping {ra.retry_after}s")
                await asyncio.sleep(ra.retry_after)
            except TelegramError as te:
                logger.error(f"Telegram Error on bot #{bot_idx + 1}: {te}")
                await asyncio.sleep(min(30, 2 ** attempt))
            except Exception as e:
                logger.error(f"Unexpected error uploading {filename}: {e}")
                await asyncio.sleep(2)
        raise RuntimeError(f"Failed to upload chunk {filename} after {retries} attempts.")

    async def upload_part(
        self,
        filepath: str,
        profile_name: str,
        thread_id: Optional[int] = None,
        retries: int = 5
    ) -> Tuple[int, int]:
        bot_idx, bot = self.get_next_bot()
        return await self.upload_part_with_bot(bot_idx, bot, filepath, profile_name, thread_id, retries)

    async def upload_parts_parallel(
        self,
        part_files: List[str],
        profile_name: str,
        thread_id: Optional[int] = None,
        on_progress: Optional[Callable[[str, int, int], None]] = None
    ) -> List[Dict[str, Any]]:
        if not part_files:
            return []

        queue = asyncio.Queue()
        for pf in part_files:
            await queue.put(pf)

        results = []
        results_lock = asyncio.Lock()

        async def worker(worker_idx: int, bot_instance: Any):
            while not queue.empty():
                try:
                    filepath = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                fname = os.path.basename(filepath)
                msg_id, bot_i = await self.upload_part_with_bot(
                    worker_idx, bot_instance, filepath, profile_name, thread_id=thread_id
                )

                async with results_lock:
                    results.append({
                        "part_name": fname,
                        "filepath": filepath,
                        "message_id": msg_id,
                        "bot_idx": bot_i
                    })

                if on_progress:
                    on_progress(fname, msg_id, bot_i)

                queue.task_done()

        workers = [
            asyncio.create_task(worker(idx, bot))
            for idx, bot in enumerate(self.bots)
        ]

        await asyncio.gather(*workers)
        return results

    async def check_message_exists(self, message_id: int) -> bool:
        if self.mock:
            matches = glob.glob(os.path.join(self.mock_cloud_dir, f"{message_id}_*"))
            return len(matches) > 0

        bot = self.get_primary_bot()
        try:
            test_fwd = await bot.forward_message(
                chat_id=self.channel_id,
                from_chat_id=self.channel_id,
                message_id=message_id
            )
            try:
                await bot.delete_message(chat_id=self.channel_id, message_id=test_fwd.message_id)
            except Exception:
                pass
            return True
        except BadRequest as br:
            if "not found" in str(br).lower():
                return False
            return True
        except Exception:
            return False

    async def upload_and_pin_catalog(self, catalog_filepath: str, thread_id: Optional[int] = None) -> int:
        msg_id, _ = await self.upload_part(catalog_filepath, "vault_catalog", thread_id=thread_id)
        if not self.mock:
            bot = self.get_primary_bot()
            try:
                await bot.pin_chat_message(chat_id=self.channel_id, message_id=msg_id, disable_notification=True)
            except Exception as e:
                logger.warning(f"Could not pin catalog #{msg_id}: {e}")
        return msg_id

    async def delete_message(self, message_id: int):
        if self.mock:
            matches = glob.glob(os.path.join(self.mock_cloud_dir, f"{message_id}_*"))
            for m in matches:
                try:
                    os.remove(m)
                except Exception:
                    pass
            return

        bot_idx, bot = self.get_next_bot()
        try:
            await bot.delete_message(chat_id=self.channel_id, message_id=message_id)
        except Exception as e:
            logger.warning(f"Could not delete message #{message_id}: {e}")

    async def download_file_by_message_id(self, message_id: int, target_path: str):
        if self.mock:
            matches = glob.glob(os.path.join(self.mock_cloud_dir, f"{message_id}_*"))
            if not matches:
                raise FileNotFoundError(f"[MOCK] Chunk with message_id {message_id} not found in mock cloud.")
            shutil.copy(matches[0], target_path)
            return

        bot_idx, bot = self.get_next_bot()
        msg = await bot.forward_message(chat_id=self.channel_id, from_chat_id=self.channel_id, message_id=message_id)
        if not msg.document:
            raise RuntimeError(f"Message #{message_id} does not contain a valid document attachment.")
        
        tg_file = await bot.get_file(msg.document.file_id)
        await tg_file.download_to_drive(custom_path=target_path)
        try:
            await bot.delete_message(chat_id=self.channel_id, message_id=msg.message_id)
        except Exception:
            pass

    async def get_pinned_catalog_message_id(self, thread_id: Optional[int] = None) -> Optional[int]:
        """
        -----------------------------------------------------------------------
        Method: get_pinned_catalog_message_id
        Description:
            Queries the Telegram channel via get_chat to retrieve the pinned
            master catalog message_id automatically for 1-click disaster recovery.
        Input parameters:
            thread_id (Optional[int]): Optional topic/thread ID.
        Return value:
            @return (Optional[int]): Pinned catalog message ID, or None.
        -----------------------------------------------------------------------
        """
        if self.mock:
            matches = glob.glob(os.path.join(self.mock_cloud_dir, "*_vault_master_catalog.json.enc"))
            if matches:
                last_cat = sorted(matches)[-1]
                return int(os.path.basename(last_cat).split("_")[0])
            return None

        bot_idx, bot = self.get_next_bot()
        try:
            chat = await bot.get_chat(chat_id=self.channel_id)
            if chat.pinned_message:
                return chat.pinned_message.message_id
        except Exception as e:
            logger.error(f"Could not retrieve pinned message from channel {self.channel_id}: {e}")
        return None
