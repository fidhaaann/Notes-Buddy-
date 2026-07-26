"""Narrow conversion boundary for legacy bot.nav values.

This is the only dialogue-foundation module permitted to import ``bot.nav``.
It does not change, clear, or otherwise mutate legacy navigation state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from bot import nav

from application.dialogue.errors import InvalidDialogueValue
from application.dialogue.models import (
    ClientSessionIdentity,
    FolderLocation,
    ItemKind,
    ResultItem,
)


def telegram_identity(
    user_id: int | str,
    chat_id: int | str,
    thread_id: int | str | None = None,
) -> ClientSessionIdentity:
    """Map current Telegram scalar IDs without importing Telegram classes."""
    return ClientSessionIdentity(
        principal_id=str(user_id),
        client_type="telegram",
        conversation_id=str(chat_id),
        thread_id=None if thread_id is None else str(thread_id),
    )


def indexed_item_to_result_item(
    item: nav.IndexedItem,
    *,
    account_id: str,
    ordinal: int | None = None,
    source: str | None = None,
) -> ResultItem:
    """Copy one legacy indexed item into an immutable typed result item."""
    resolved_ordinal = ordinal
    if resolved_ordinal is None:
        if not item.full_index.isdigit():
            raise InvalidDialogueValue(
                "legacy item full_index must be a positive flat index"
            )
        resolved_ordinal = int(item.full_index)

    if item.is_shortcut:
        item_kind = ItemKind.SHORTCUT
        target_kind = (
            ItemKind.FOLDER
            if item.shortcut_target_mime_type == nav.FOLDER_MIME
            else ItemKind.FILE
            if item.shortcut_target_mime_type
            else None
        )
    else:
        item_kind = ItemKind.FOLDER if item.is_folder else ItemKind.FILE
        target_kind = None

    return ResultItem(
        ordinal=resolved_ordinal,
        item_id=item.id,
        account_id=account_id,
        name_snapshot=item.name,
        item_kind=item_kind,
        mime_type=item.mime_type or None,
        parent_ids=(),
        is_shortcut=item.is_shortcut,
        shortcut_target_id=item.shortcut_target_id,
        shortcut_target_kind=target_kind,
        source=source,
        capabilities=frozenset(),
    )


def index_map_to_result_items(
    index_map: Mapping[str, nav.IndexedItem],
    *,
    account_id: str,
    source: str | None = None,
) -> tuple[ResultItem, ...]:
    """Convert current flat visible order without mutating the index map."""
    converted: list[ResultItem] = []
    for visible_index, item in index_map.items():
        if not visible_index.isdigit() or int(visible_index) <= 0:
            raise InvalidDialogueValue(
                "legacy active views must use positive flat indices"
            )
        converted.append(
            indexed_item_to_result_item(
                item,
                account_id=account_id,
                ordinal=int(visible_index),
                source=source,
            )
        )
    return tuple(converted)


def active_view_to_result_items(
    view: nav.ViewContext,
    *,
    account_id: str,
) -> tuple[ResultItem, ...]:
    return index_map_to_result_items(
        view.index_map,
        account_id=account_id,
        source=view.view_type,
    )


def result_item_to_indexed_item(
    item: ResultItem,
    *,
    path: str = "",
) -> nav.IndexedItem:
    """Create a legacy execution value from an already-resolved typed item."""
    shortcut_is_folder = (
        item.item_kind is ItemKind.SHORTCUT
        and item.shortcut_target_kind is ItemKind.FOLDER
    )
    return nav.IndexedItem(
        id=item.item_id,
        name=item.name_snapshot,
        mime_type=item.mime_type or "",
        is_folder=item.item_kind is ItemKind.FOLDER or shortcut_is_folder,
        parent_index="",
        full_index=str(item.ordinal),
        is_shortcut=item.item_kind is ItemKind.SHORTCUT,
        shortcut_target_id=item.shortcut_target_id,
        shortcut_target_mime_type=(
            nav.FOLDER_MIME
            if item.shortcut_target_kind is ItemKind.FOLDER
            else item.mime_type
            if item.shortcut_target_kind is ItemKind.FILE
            else None
        ),
        path=path,
    )


def folder_stack_to_locations(
    stack: Iterable[tuple[str, str]],
    *,
    shared_drive_id: str | None = None,
) -> tuple[FolderLocation, ...]:
    """Copy a legacy ``[(id, name), ...]`` path into typed locations."""
    locations: list[FolderLocation] = []
    parent_id: str | None = None
    for item_id, name in stack:
        location = FolderLocation(
            item_id=item_id,
            name=name,
            parent_id=parent_id,
            shared_drive_id=shared_drive_id,
        )
        locations.append(location)
        parent_id = item_id
    return tuple(locations)
