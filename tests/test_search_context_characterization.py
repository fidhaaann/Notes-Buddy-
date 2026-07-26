"""Characterization tests for the two current result-context mechanisms."""

import copy
import unittest
from unittest.mock import patch

from bot import commands, nav
from nlp import context as nlp_context
from nlp import router
from nlp.intents import Intent, IntentType

from tests.helpers import make_update_context


def _search_results(prefix: str = "dbms") -> list[dict]:
    return [
        {
            "file_id": f"{prefix}-1",
            "name": "DBMS Notes.pdf",
            "mime_type": "application/pdf",
        },
        {
            "file_id": f"{prefix}-2",
            "name": "DBMS Lecture.mp4",
            "mime_type": "video/mp4",
        },
    ]


def _nav_item(item_id: str, name: str, index: str) -> nav.IndexedItem:
    return nav.IndexedItem(
        id=item_id,
        name=name,
        mime_type="application/pdf",
        is_folder=False,
        parent_index="",
        full_index=index,
        path="Search",
    )


class SearchContextSeparationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        nav._sessions.clear()

    def tearDown(self) -> None:
        nav._sessions.clear()

    async def test_command_search_populates_navigation_view_only(self) -> None:
        """Current /search does not populate nlp.context SearchContext."""
        uid = 101
        update, context = make_update_context(uid, "/search dbms")

        with (
            patch.object(commands, "_is_authenticated", return_value=True),
            patch.object(commands, "_check_rate_limit", return_value=False),
            patch.object(
                commands.indexed_search,
                "search_index",
                return_value=_search_results(),
            ),
        ):
            await commands.cmd_search(update, context)

        active_view = nav.get_active_view(uid)
        self.assertIsNotNone(active_view)
        self.assertEqual(active_view.view_type, "search")
        self.assertEqual(nav.resolve_index(uid, "1").id, "dbms-1")
        self.assertIsNone(nlp_context.get_search_context(context.user_data))

    async def test_command_search_replaces_nav_but_leaves_existing_nlp_context_stale(self) -> None:
        uid = 101
        update, context = make_update_context(uid, "/search new")
        nlp_context.set_search_context(
            context.user_data,
            _search_results("old"),
            query="old",
        )
        old_context = copy.deepcopy(context.user_data["_search_context"])

        with (
            patch.object(commands, "_is_authenticated", return_value=True),
            patch.object(commands, "_check_rate_limit", return_value=False),
            patch.object(
                commands.indexed_search,
                "search_index",
                return_value=_search_results("new"),
            ),
        ):
            await commands.cmd_search(update, context)

        self.assertEqual(nav.resolve_index(uid, "1").id, "new-1")
        self.assertEqual(context.user_data["_search_context"], old_context)
        self.assertEqual(nlp_context.resolve_reference(context.user_data, "1")["file_id"], "old-1")

    async def test_nlp_search_currently_populates_both_context_stores(self) -> None:
        uid = 101
        update, context = make_update_context(uid, "find dbms")
        intent = Intent(
            intent=IntentType.SEARCH,
            confidence=0.9,
            raw_text="find dbms",
            query="dbms",
            search_scope="entire_drive",
        )

        with patch.object(
            router.indexed_search,
            "search_index",
            return_value=_search_results(),
        ):
            await router._handle_search(update, context, intent)

        self.assertEqual(nav.resolve_index(uid, "2").id, "dbms-2")
        results, query = nlp_context.get_active_results(context.user_data)
        self.assertEqual(query, "dbms")
        self.assertEqual([item["file_id"] for item in results], ["dbms-1", "dbms-2"])

    def test_replacing_navigation_view_does_not_synchronize_nlp_search_context(self) -> None:
        user_data: dict = {}
        nlp_context.set_search_context(user_data, _search_results("old"), query="old")
        nav.set_active_view(
            101,
            "search",
            {"1": _nav_item("new-1", "New.pdf", "1")},
            metadata={"keyword": "new"},
        )

        self.assertEqual(nav.resolve_index(101, "1").id, "new-1")
        self.assertEqual(nlp_context.resolve_reference(user_data, "1")["file_id"], "old-1")

    def test_search_context_is_isolated_by_telegram_user_data_container(self) -> None:
        user_a: dict = {}
        user_b: dict = {}
        nlp_context.set_search_context(user_a, _search_results("a"), query="alpha")
        nlp_context.set_search_context(user_b, _search_results("b"), query="beta")

        self.assertEqual(nlp_context.resolve_reference(user_a, "1")["file_id"], "a-1")
        self.assertEqual(nlp_context.resolve_reference(user_b, "1")["file_id"], "b-1")


class SearchReferenceCharacterizationTests(unittest.TestCase):
    def test_classifier_distinguishes_fresh_search_from_follow_up_references(self) -> None:
        self.assertEqual(
            nlp_context.classify_query("find my DBMS notes"),
            nlp_context.QueryType.FRESH_QUERY,
        )
        self.assertEqual(
            nlp_context.classify_query("the second one"),
            nlp_context.QueryType.FOLLOW_UP,
        )
        self.assertEqual(
            nlp_context.classify_query("2"),
            nlp_context.QueryType.FOLLOW_UP,
        )
        self.assertEqual(
            nlp_context.classify_query("download 2"),
            nlp_context.QueryType.FOLLOW_UP,
        )
        self.assertEqual(
            nlp_context.classify_query("open 1"),
            nlp_context.QueryType.FOLLOW_UP,
        )

    def test_fresh_signal_wins_when_query_also_contains_an_ordinal(self) -> None:
        self.assertEqual(
            nlp_context.classify_query("find the first DBMS lecture"),
            nlp_context.QueryType.FRESH_QUERY,
        )

    def test_search_reference_helper_resolves_numeric_ordinal_last_name_and_type(self) -> None:
        user_data: dict = {}
        nlp_context.set_search_context(user_data, _search_results(), query="dbms")

        self.assertEqual(nlp_context.resolve_reference(user_data, "1")["file_id"], "dbms-1")
        self.assertEqual(nlp_context.resolve_reference(user_data, "second")["file_id"], "dbms-2")
        self.assertEqual(nlp_context.resolve_reference(user_data, "last")["file_id"], "dbms-2")
        self.assertEqual(nlp_context.resolve_reference(user_data, "notes")["file_id"], "dbms-1")
        self.assertEqual(nlp_context.resolve_reference(user_data, "video")["file_id"], "dbms-2")
        self.assertIsNone(nlp_context.resolve_reference(user_data, "3"))


class SearchContextExpiryCharacterizationTests(unittest.TestCase):
    def test_unexpired_search_context_remains_available(self) -> None:
        user_data: dict = {}
        with patch("nlp.context.time.time", return_value=100.0):
            nlp_context.set_search_context(user_data, _search_results(), query="dbms")

        with patch(
            "nlp.context.time.time",
            return_value=100.0 + nlp_context.SEARCH_CONTEXT_TTL - 1.0,
        ):
            results, query = nlp_context.get_active_results(user_data)

        self.assertEqual(len(results), 2)
        self.assertEqual(query, "dbms")

    def test_expired_search_context_is_rejected_but_raw_state_is_retained(self) -> None:
        user_data: dict = {}
        with patch("nlp.context.time.time", return_value=100.0):
            nlp_context.set_search_context(user_data, _search_results(), query="dbms")

        with patch(
            "nlp.context.time.time",
            return_value=100.0 + nlp_context.SEARCH_CONTEXT_TTL,
        ):
            self.assertFalse(nlp_context.is_search_context_valid(user_data))
            self.assertEqual(nlp_context.get_active_results(user_data), ([], ""))
            self.assertIsNone(nlp_context.resolve_reference(user_data, "1"))

        self.assertIn("_search_context", user_data)

    def test_expiry_of_one_users_context_does_not_affect_another(self) -> None:
        user_a: dict = {}
        user_b: dict = {}
        nlp_context.set_search_context(user_a, _search_results("a"), query="alpha")
        nlp_context.set_search_context(user_b, _search_results("b"), query="beta")
        user_a["_search_context"]["timestamp"] = 100.0
        user_b["_search_context"]["timestamp"] = 100.0 + nlp_context.SEARCH_CONTEXT_TTL

        with patch(
            "nlp.context.time.time",
            return_value=100.0 + nlp_context.SEARCH_CONTEXT_TTL + 1.0,
        ):
            self.assertEqual(nlp_context.get_active_results(user_a), ([], ""))
            results_b, query_b = nlp_context.get_active_results(user_b)

        self.assertEqual(query_b, "beta")
        self.assertEqual(results_b[0]["file_id"], "b-1")


if __name__ == "__main__":
    unittest.main()
