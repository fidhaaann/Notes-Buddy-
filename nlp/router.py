"""NLP routing for natural language messages."""

from __future__ import annotations

import re
from typing import Optional

from rapidfuzz import process, fuzz

from bot import formatter, nav, ui
from db import models
from drive import drive_service as ds
from indexing import indexer, search as indexed_search
from nlp import context as nlp_context
from nlp import intents as intent_types
from nlp import normalize
from security import validators
from services import anomaly_detection, stepup_auth
from services import parser as parser_utils
from tasks.manager import get_task_manager


_DOWNLOAD_KEYWORDS = {"download", "send", "give"}
_UPLOAD_KEYWORDS = {"upload", "save", "store", "put"}
_SEARCH_KEYWORDS = {"search", "find", "look", "show"}
_OPEN_KEYWORDS = {"open", "enter", "go", "goto"}
_BACK_KEYWORDS = {"back", "previous", "up"}
_PWD_KEYWORDS = {"where", "path", "pwd"}
_INFO_KEYWORDS = {"info", "details", "metadata"}
_DELETE_KEYWORDS = {"delete", "remove"}
_RENAME_KEYWORDS = {"rename"}
_MOVE_KEYWORDS = {"move"}
_HELP_KEYWORDS = {"help", "commands"}
_INDEX_KEYWORDS = {"index", "refresh"}


def _extract_target(text: str) -> str | None:
    match = re.search(r"(?:to|in|into|on|for)\s+(.+)$", text)
    if match:
        return match.group(1).strip()
    return None


def _folder_candidates(uid: int) -> dict[str, nav.IndexedItem]:
    view = nav.get_active_view(uid)
    if not view:
        return {}
    return {item.name: item for item in view.index_map.values() if item.is_folder}


def _file_candidates(uid: int) -> dict[str, nav.IndexedItem]:
    view = nav.get_active_view(uid)
    if not view:
        return {}
    return {item.name: item for item in view.index_map.values() if not item.is_folder}


def interpret_intent(text: str) -> intent_types.Intent:
    raw = text.strip()
    normalized = normalize.normalize_text(raw)
    if not normalized:
        return intent_types.Intent(intent_types.IntentType.UNKNOWN, 0.0, raw_text=raw)

    if any(k in normalized for k in _HELP_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.HELP, 0.95, raw_text=raw)

    if any(k in normalized for k in _BACK_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.BACK, 0.9, raw_text=raw)

    if "current folder" in normalized and any(k in normalized for k in {"show", "list", "browse"}):
        return intent_types.Intent(intent_types.IntentType.BROWSE, 0.8, raw_text=raw)

    if "current folder" in normalized or "current path" in normalized:
        return intent_types.Intent(intent_types.IntentType.PWD, 0.85, raw_text=raw)

    if any(k in normalized for k in _PWD_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.PWD, 0.9, raw_text=raw)

    if any(k in normalized for k in _INDEX_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.INDEX, 0.8, raw_text=raw)

    idx = normalize.extract_index(normalized)

    if any(k in normalized for k in _DOWNLOAD_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.DOWNLOAD, 0.85, raw_text=raw, index=idx, query=normalized)

    if any(k in normalized for k in _UPLOAD_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.UPLOAD,
            0.8,
            raw_text=raw,
            target_name=_extract_target(normalized),
        )

    if any(k in normalized for k in _DELETE_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.DELETE, 0.75, raw_text=raw, index=idx, query=normalized, needs_confirmation=True)

    if any(k in normalized for k in _RENAME_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.RENAME, 0.75, raw_text=raw, index=idx, query=normalized, needs_confirmation=True)

    if any(k in normalized for k in _MOVE_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.MOVE, 0.75, raw_text=raw, index=idx, query=normalized, needs_confirmation=True)

    if any(k in normalized for k in _INFO_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.INFO, 0.7, raw_text=raw, index=idx, query=normalized)

    if any(k in normalized for k in _OPEN_KEYWORDS):
        return intent_types.Intent(
            intent_types.IntentType.OPEN_FOLDER,
            0.75,
            raw_text=raw,
            target_name=_extract_target(normalized),
            query=normalized,
        )

    if re.fullmatch(r"(list|browse|show files|show folders|show current folder)", normalized):
        return intent_types.Intent(intent_types.IntentType.BROWSE, 0.8, raw_text=raw)

    if any(k in normalized for k in _SEARCH_KEYWORDS):
        return intent_types.Intent(intent_types.IntentType.SEARCH, 0.7, raw_text=raw, query=normalized)

    best_action, score = normalize.best_action_token(normalized)
    if best_action and score >= 80:
        return intent_types.Intent(intent_types.IntentType.SEARCH, 0.6, raw_text=raw, query=normalized)

    return intent_types.Intent(intent_types.IntentType.UNKNOWN, 0.0, raw_text=raw)


async def handle_nlp_message(update, context) -> bool:
    if not update.message or not update.message.text:
        return False
    assert context.user_data is not None
    uid = update.effective_user.id

    if nlp_context.is_expired(context.user_data):
        nlp_context.clear_state(context.user_data)

    text = update.message.text.strip()
    intent = interpret_intent(text)

    if intent.intent not in {intent_types.IntentType.HELP, intent_types.IntentType.UNKNOWN}:
        if not models.get_user(uid):
            await update.message.reply_text(formatter.login_required())
            return True

    if intent.intent == intent_types.IntentType.UNKNOWN:
        await update.message.reply_text(formatter.nlp_ambiguous_action())
        return True

    if intent.intent == intent_types.IntentType.HELP:
        await update.message.reply_text(
            formatter.tools_menu(),
            reply_markup=ui.back_to_menu_keyboard(),
        )
        return True

    if intent.intent == intent_types.IntentType.BACK:
        if not nav.pop_folder(uid):
            await update.message.reply_text(formatter.current_path(nav.breadcrumb(uid)))
            return True
        await update.message.reply_text(formatter.current_path(nav.breadcrumb(uid)))
        return True

    if intent.intent == intent_types.IntentType.PWD:
        await update.message.reply_text(formatter.current_path(nav.breadcrumb(uid)))
        return True

    if intent.intent == intent_types.IntentType.INDEX:
        await _handle_index_folder(update, context)
        return True

    if intent.intent == intent_types.IntentType.BROWSE:
        await _handle_browse(update, context)
        return True

    if intent.intent == intent_types.IntentType.SEARCH:
        await _handle_search(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.OPEN_FOLDER:
        await _handle_open_folder(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.DOWNLOAD:
        await _handle_download(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.INFO:
        await _handle_info(update, context, intent)
        return True

    if intent.intent == intent_types.IntentType.UPLOAD:
        await _handle_upload_hint(update, context, intent)
        return True

    if intent.intent in {
        intent_types.IntentType.DELETE,
        intent_types.IntentType.RENAME,
        intent_types.IntentType.MOVE,
    }:
        await _handle_sensitive(update, context, intent)
        return True

    return False


async def _handle_search(update, context, intent: intent_types.Intent) -> None:
    uid = update.effective_user.id
    query = intent.query or ""
    results = indexed_search.search_index(uid, query)
    if not results:
        suggestions = indexed_search.suggest_files(uid, query)
        if suggestions:
            labels = [s["name"] for s in suggestions]
            await update.message.reply_text(formatter.nlp_suggestions("Closest Matches", labels))
            _set_suggestion_view(uid, suggestions, label_prefix="Match")
            return
        await update.message.reply_text(formatter.nlp_no_results(query))
        return

    index_map: dict[str, nav.IndexedItem] = {}
    for i, item in enumerate(results, 1):
        idx = str(i)
        index_map[idx] = nav.IndexedItem(
            id=item["file_id"],
            name=item["name"],
            mime_type=item.get("mime_type") or "",
            is_folder=False,
            parent_index="",
            full_index=idx,
            is_shortcut=False,
            shortcut_target_id=None,
            shortcut_target_mime_type=None,
            path=f"Search: {query}",
        )
    nav.set_active_view(uid, "search", index_map, metadata={"keyword": query})
    await update.message.reply_text(
        formatter.search_results_indexed(query, index_map),
        reply_markup=ui.back_to_menu_keyboard(),
    )


async def _handle_open_folder(update, context, intent: intent_types.Intent) -> None:
    uid = update.effective_user.id
    if intent.index:
        item = nav.resolve_index(uid, intent.index)
        if not item or not item.is_folder:
            await update.message.reply_text(formatter.error("Invalid folder selection."))
            return
        target_id = item.shortcut_target_id if item.is_shortcut and item.shortcut_target_id else item.id
        if nav.is_in_stack(uid, target_id):
            await update.message.reply_text(formatter.error("Navigation loop detected.", "Folder is already in your path."))
            return
        nav.push_folder(uid, target_id, item.name)
        await update.message.reply_text(formatter.current_path(nav.breadcrumb(uid)))
        return
    target = intent.target_name or intent.query or ""
    candidates = _folder_candidates(uid)
    if not candidates:
        await update.message.reply_text(
            formatter.error("No active folder list.", "Use /info to list folders first.")
        )
        return
    matches = process.extract(
        target,
        candidates.keys(),
        scorer=fuzz.WRatio,
        limit=5,
    )
    if not matches:
        await update.message.reply_text(formatter.error("No matching folder found."))
        return
    name, score, _ = matches[0]
    item = candidates.get(name)
    if not item:
        await update.message.reply_text(formatter.error("No matching folder found."))
        return
    if score < 70:
        labels = [m[0] for m in matches]
        await update.message.reply_text(formatter.nlp_suggestions("Closest Folders", labels))
        _set_folder_suggestion_view(uid, [candidates[m[0]] for m in matches if m[0] in candidates])
        return
    if item.is_shortcut and item.shortcut_target_id:
        target_id = item.shortcut_target_id
    else:
        target_id = item.id
    if nav.is_in_stack(uid, target_id):
        await update.message.reply_text(formatter.error("Navigation loop detected.", "Folder is already in your path."))
        return
    nav.push_folder(uid, target_id, item.name)
    await update.message.reply_text(formatter.current_path(nav.breadcrumb(uid)))


async def _handle_download(update, context, intent: intent_types.Intent) -> None:
    uid = update.effective_user.id
    index = intent.index
    if not index:
        candidates = _file_candidates(uid)
        if not candidates:
            await update.message.reply_text(
                formatter.error("Which file?", "Say 'download 1' or 'download the second one'.")
            )
            return
        query = intent.query or intent.raw_text
        matches = process.extract(
            query,
            candidates.keys(),
            scorer=fuzz.WRatio,
            limit=3,
        )
        if matches:
            best_name, best_score, _ = matches[0]
            second_score = matches[1][1] if len(matches) > 1 else 0
            if best_score >= 85 and (best_score - second_score) >= 10:
                item = candidates.get(best_name)
                if item:
                    await _download_item(update, context, item)
                    return
            labels = [m[0] for m in matches]
            await update.message.reply_text(formatter.nlp_suggestions("Closest Files", labels))
            _set_file_suggestion_view(uid, [candidates[m[0]] for m in matches if m[0] in candidates])
            return
        await update.message.reply_text(
            formatter.error("Which file?", "Say 'download 1' or 'download the second one'.")
        )
        return
    item = nav.resolve_index(uid, index)
    if not item or item.is_folder:
        await update.message.reply_text(formatter.error("Invalid file selection."))
        return
    await _download_item(update, context, item)


async def _download_item(update, context, item: nav.IndexedItem) -> None:
    uid = update.effective_user.id
    if not await _require_stepup_nlp(update, context, "download files"):
        return
    if await anomaly_detection.check_anomaly(uid, "download"):
        await update.message.reply_text(formatter.error("Unusual activity detected."))
        return
    meta = await ds.get_file_metadata_async(uid, item.id)
    size_raw = int(meta["size"]) if meta.get("size") else 0
    if size_raw > 0 and size_raw > ds.MAX_DOWNLOAD_BYTES:
        await update.message.reply_text(
            formatter.download_too_large(
                meta.get("name", item.name),
                parser_utils.human_size(size_raw),
                meta.get("webViewLink", ""),
                meta.get("webContentLink", ""),
            )
        )
        return
    manager = get_task_manager(context)
    if not manager:
        await update.message.reply_text(formatter.error("Background queue unavailable."))
        return
    assert update.effective_chat is not None
    await manager.enqueue_download(
        telegram_id=uid,
        chat_id=update.effective_chat.id,
        file_id=item.id,
        filename=meta.get("name", item.name),
        size_str="Unknown" if not size_raw else parser_utils.human_size(size_raw),
    )


async def _handle_info(update, context, intent: intent_types.Intent) -> None:
    uid = update.effective_user.id
    index = intent.index
    if not index:
        await update.message.reply_text(formatter.error("Which file?", "Say 'details of 1'."))
        return
    item = nav.resolve_index(uid, index)
    if not item:
        await update.message.reply_text(formatter.error("Invalid selection."))
        return
    meta = await ds.get_file_metadata_async(uid, item.id)
    meta["_path"] = item.path
    is_fav = models.is_favorite(uid, item.id)
    await update.message.reply_text(
        formatter.file_info(meta),
        reply_markup=ui.file_actions_keyboard(item.id, is_fav),
    )


async def _handle_upload_hint(update, context, intent: intent_types.Intent) -> None:
    assert context.user_data is not None
    target = intent.target_name
    if target:
        context.user_data["pending_upload_target"] = target
        await update.message.reply_text(
            formatter.success("Upload Target Set", target),
        )
        return
    await update.message.reply_text(
        formatter.upload_mode_enabled()
    )


async def _handle_sensitive(update, context, intent: intent_types.Intent) -> None:
    assert context.user_data is not None
    uid = update.effective_user.id
    index = intent.index
    if not index:
        await update.message.reply_text(formatter.nlp_clarify("Which item?"))
        return
    item = nav.resolve_index(uid, index)
    if not item:
        await update.message.reply_text(formatter.error("Invalid selection."))
        return
    pending = {
        "intent": intent.intent.value,
        "file_id": item.id,
        "name": item.name,
        "index": index,
    }

    if intent.intent == intent_types.IntentType.RENAME:
        new_name = _extract_after_to(intent.raw_text)
        if not new_name:
            pending["awaiting_name"] = True
            context.user_data["pending_action"] = pending
            await update.message.reply_text(
                formatter.nlp_clarify("What should I rename it to?")
            )
            return
        pending["new_name"] = new_name

    if intent.intent == intent_types.IntentType.MOVE:
        target = _extract_target(intent.raw_text)
        if not target:
            pending["awaiting_target"] = True
            context.user_data["pending_action"] = pending
            await update.message.reply_text(
                formatter.nlp_clarify("Where should I move it?")
            )
            return
        pending["target_name"] = target

    context.user_data["pending_action"] = pending
    await update.message.reply_text(
        formatter.confirm_action(intent.intent.value.capitalize(), item.name)
    )


async def _handle_index_folder(update, context) -> None:
    uid = update.effective_user.id
    fid = nav.current_folder_id(uid)
    listing = await ds.list_directory_async(uid, parent_id=fid)
    tasks = []
    for f in listing.files:
        file_id = f.get("id")
        if not file_id:
            continue
        name = f.get("name") or "file"
        mime = f.get("mimeType", "")
        parent_id = fid
        indexer.upsert_metadata(uid, file_id, name, mime, parent_id, None, None)
        tasks.append(file_id)
    manager = get_task_manager(context)
    if not manager:
        await update.message.reply_text(formatter.error("Background queue unavailable."))
        return
    for file_id in tasks:
        await manager.enqueue_index(uid, file_id)
    await update.message.reply_text(
        formatter.success("Indexing Started", nav.breadcrumb(uid))
    )


async def _handle_browse(update, context) -> None:
    uid = update.effective_user.id
    fid = nav.current_folder_id(uid)
    listing = await ds.list_directory_async(uid, parent_id=fid)
    folders = listing.folders
    files = listing.files
    for item in files:
        indexer.upsert_metadata(
            uid,
            item.get("id", ""),
            item.get("name", "file"),
            item.get("mimeType"),
            fid,
            int(item.get("size") or 0) if item.get("size") else None,
            None,
        )
    index_map = nav.build_flat_index_map(uid, folders, files)
    nav.set_active_view(uid, "folder", index_map, metadata={"folder_id": fid})
    text = formatter.directory_listing(nav.breadcrumb(uid), index_map, folders, files)
    await update.message.reply_text(text, reply_markup=ui.browse_keyboard(is_root=(fid == "root")))


def _set_suggestion_view(uid: int, suggestions: list[dict], label_prefix: str) -> None:
    index_map: dict[str, nav.IndexedItem] = {}
    for i, item in enumerate(suggestions, 1):
        idx = str(i)
        index_map[idx] = nav.IndexedItem(
            id=item["file_id"],
            name=item["name"],
            mime_type=item.get("mime_type") or "",
            is_folder=False,
            parent_index="",
            full_index=idx,
            is_shortcut=False,
            shortcut_target_id=None,
            shortcut_target_mime_type=None,
            path=f"{label_prefix} Suggestions",
        )
    nav.set_active_view(uid, "nlp_suggestions", index_map)


def _set_file_suggestion_view(uid: int, items: list[nav.IndexedItem]) -> None:
    index_map: dict[str, nav.IndexedItem] = {}
    for i, item in enumerate(items, 1):
        idx = str(i)
        index_map[idx] = nav.IndexedItem(
            id=item.id,
            name=item.name,
            mime_type=item.mime_type,
            is_folder=False,
            parent_index="",
            full_index=idx,
            is_shortcut=False,
            shortcut_target_id=None,
            shortcut_target_mime_type=None,
            path="File Suggestions",
        )
    nav.set_active_view(uid, "nlp_file_suggestions", index_map)


def _set_folder_suggestion_view(uid: int, items: list[nav.IndexedItem]) -> None:
    index_map: dict[str, nav.IndexedItem] = {}
    for i, item in enumerate(items, 1):
        idx = str(i)
        index_map[idx] = nav.IndexedItem(
            id=item.id,
            name=item.name,
            mime_type=item.mime_type,
            is_folder=True,
            parent_index="",
            full_index=idx,
            is_shortcut=item.is_shortcut,
            shortcut_target_id=item.shortcut_target_id,
            shortcut_target_mime_type=item.shortcut_target_mime_type,
            path="Folder Suggestions",
        )
    nav.set_active_view(uid, "nlp_folder_suggestions", index_map)


def _extract_after_to(text: str) -> str | None:
    match = re.search(r"\\bto\\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


async def _require_stepup_nlp(update, context, action_label: str) -> bool:
    assert context.user_data is not None
    uid = update.effective_user.id
    result = await stepup_auth.request_verification(uid, action_label)
    status = result.get("status")
    if status == "verified":
        context.user_data.pop("awaiting_email", None)
        context.user_data.pop("awaiting_otp", None)
        context.user_data.pop("pending_stepup_action", None)
        return True
    if status == "no_email":
        context.user_data["awaiting_email"] = True
        context.user_data["pending_stepup_action"] = action_label
        await update.message.reply_text(
            formatter.stepup_email_required(action_label),
            reply_markup=ui.stepup_email_entry_keyboard(),
        )
        return False
    if status == "email_failed":
        await update.message.reply_text(formatter.stepup_email_failed())
        return False
    if status == "sent":
        context.user_data["awaiting_otp"] = True
        context.user_data["pending_stepup_action"] = action_label
        await update.message.reply_text(
            formatter.stepup_code_sent(action_label, result.get("email", ""), result.get("ttl", 10)),
            reply_markup=ui.stepup_resend_keyboard(action_label),
        )
        return False
    if status == "cooldown":
        context.user_data["awaiting_otp"] = True
        context.user_data["pending_stepup_action"] = action_label
        await update.message.reply_text(
            formatter.stepup_code_pending(action_label, result.get("email", ""), result.get("retry_after", 60)),
            reply_markup=ui.stepup_resend_keyboard(action_label),
        )
        return False
    await update.message.reply_text(formatter.error("Verification required.", "Use /verify <code>"))
    return False


async def handle_pending_action(update, context) -> bool:
    if not update.message or not update.message.text:
        return False
    assert context.user_data is not None
    pending = context.user_data.get("pending_action")
    if not pending:
        return False
    text = update.message.text.strip().lower()
    if pending.get("awaiting_name"):
        pending["new_name"] = update.message.text.strip()
        pending.pop("awaiting_name", None)
        context.user_data["pending_action"] = pending
        await update.message.reply_text(
            formatter.confirm_action("Rename", pending.get("name", "file"))
        )
        return True
    if pending.get("awaiting_target"):
        pending["target_name"] = update.message.text.strip()
        pending.pop("awaiting_target", None)
        context.user_data["pending_action"] = pending
        await update.message.reply_text(
            formatter.confirm_action("Move", pending.get("name", "file"))
        )
        return True
    if text in {"cancel", "no", "stop"}:
        context.user_data.pop("pending_action", None)
        await update.message.reply_text(formatter.success("Cancelled"))
        return True
    if text not in {"confirm", "yes", "ok", "proceed"}:
        await update.message.reply_text(formatter.nlp_clarify("Reply with confirm or cancel."))
        return True

    await _execute_pending_action(update, context, pending)
    context.user_data.pop("pending_action", None)
    return True


async def _execute_pending_action(update, context, pending: dict) -> None:
    uid = update.effective_user.id
    action = pending.get("intent")
    file_id = pending.get("file_id")
    if not file_id or not validators.validate_drive_id(file_id, allow_root=False):
        await update.message.reply_text(formatter.error("Invalid file reference."))
        return
    if action == intent_types.IntentType.DELETE.value:
        if not await _require_stepup_nlp(update, context, "delete files"):
            return
        await ds.delete_file_async(uid, file_id)
        await update.message.reply_text(formatter.success("Deleted"))
        return
    if action == intent_types.IntentType.RENAME.value:
        new_name = pending.get("new_name")
        if not new_name:
            await update.message.reply_text(formatter.error("Missing new name."))
            return
        updated = await ds.rename_file_async(uid, file_id, new_name)
        await update.message.reply_text(formatter.success("Renamed", updated.get("name")))
        return
    if action == intent_types.IntentType.MOVE.value:
        dest_id = pending.get("dest_id")
        dest_name = pending.get("dest_name")
        if not dest_id:
            target_name = pending.get("target_name")
            if not target_name:
                await update.message.reply_text(formatter.error("Missing destination folder."))
                return
            candidates = _folder_candidates(uid)
            if not candidates:
                await update.message.reply_text(formatter.error("No folder list available.", "Use /info first."))
                return
            match = process.extractOne(target_name, candidates.keys(), scorer=fuzz.WRatio)
            if not match or match[1] < 70:
                await update.message.reply_text(formatter.nlp_suggestions("Closest Folders", list(candidates.keys())[:5]))
                return
            dest = candidates.get(match[0])
            if not dest:
                await update.message.reply_text(formatter.error("Destination not found."))
                return
            dest_id = dest.shortcut_target_id if dest.is_shortcut and dest.shortcut_target_id else dest.id
            dest_name = dest.name
        await ds.move_file_async(uid, file_id, dest_id)
        await update.message.reply_text(formatter.success("Moved", pending.get("name"), dest_name))
        return
