"""Unified chat -> competitor-setup flow: routing, product resolution, stored result; mocked model and search."""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from langchain_core.messages import AIMessage

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
sys.path.insert(0, os.path.dirname(__file__))

from app.modules.competitive_intelligence.models import MyProduct
from app.modules.competitive_intelligence.scout_discovery import ScoutDiscovery
from app.modules.competitive_intelligence.service import IntelligenceService
from app.modules.competitive_intelligence.setup_recommend import ProductBrief
from app.modules.conversations.service import ConversationService
from test_modular_monolith import FakeConversationRepository, FakeExecutionPort

SETUP = {
    "product": {"name": "NoteNest", "description": "a markdown app", "source": "default",
                "my_product_id": str(uuid4())},
    "recommendation": {"products": [{"name": "Obsidian", "kind": "competitor"},
                                    {"name": "Notion", "kind": "competitor"}], "follow_up_question": None},
}
ASK = {"product": {"name": "", "description": "", "source": "message", "my_product_id": None},
       "recommendation": {"products": [], "follow_up_question": "What does your product do?"}}


class ScriptedPort(FakeExecutionPort):
    """Returns the scripted decision, or raises; counts Supervisor calls."""

    def __init__(self, decision: str = "answer", fail: bool = False) -> None:
        super().__init__()
        self.decision, self.fail, self.calls = decision, fail, 0

    async def create_plan(self, run_id, initial_state, on_assistant_token=None):
        self.calls += 1
        if self.fail:
            raise ConnectionError("model offline")
        if self.decision == "propose_plan":
            return await super().create_plan(run_id, initial_state, on_assistant_token)
        return {"mode": "conversation", "plan": [], "messages": [AIMessage(content="Sure.")],
                "metadata": {"supervisor_decision": self.decision}}


class FakeSetup:
    def __init__(self, *results, reply: str | None = "Found some competitors.") -> None:
        self.results, self.prompts, self.reply, self.replied = list(results), [], reply, []

    async def run_setup(self, owner_id: str, prompt: str):
        self.prompts.append(prompt)
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


    async def write_reply(self, message, setup):
        self.replied.append((message, setup))
        if self.reply is None:
            raise TimeoutError("model slow")
        return self.reply


class ChatSetupRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, port, setup, text):
        if not hasattr(self, "service"):
            self.repository = FakeConversationRepository()
            self.service = ConversationService(self.repository, port, setup_port=setup)
            self.conversation = await self.service.create_conversation(title="Setup")
        accepted = await self.service.start_message(self.conversation.id, text)
        self.assertTrue(await self.service.process_next_turn(worker_id="w"))
        messages = [m for m in await self.service.list_messages(self.conversation.id) if m.role == "assistant"]
        conversation = await self.service.get_conversation(self.conversation.id)
        return accepted, messages[-1], conversation

    async def test_model_choice_runs_setup_and_stores_the_result_on_the_message(self) -> None:
        setup = FakeSetup(SETUP)
        _, message, conversation = await self.start(ScriptedPort("setup_tracking"), setup, "Find my competitors")
        self.assertEqual(message.metadata["outcome"], "setup_tracking")
        self.assertEqual(message.metadata["ci_setup"], SETUP)
        self.assertTrue(message.content.startswith("Found some competitors.\n\nUsing: NoteNest (default)"))
        self.assertNotIn("Sure.", message.content)
        self.assertEqual(setup.replied[0][0], "Find my competitors")
        self.assertIn("Suggested competitors: Obsidian, Notion.", message.content)
        self.assertNotIn("ci_setup_pending", conversation.metadata)
        self.assertNotIn("ci_setup", conversation.metadata)

    async def test_keyword_match_overrides_propose_plan(self) -> None:
        port = ScriptedPort("propose_plan")
        _, message, conversation = await self.start(port, FakeSetup(SETUP), "Monitor my competitors' pricing")
        self.assertEqual((port.calls, conversation.draft_plan), (1, []))
        self.assertEqual(message.metadata["outcome"], "setup_tracking")
        self.assertNotIn("drafted a workflow", message.content)

    async def test_keyword_match_overrides_clarify(self) -> None:
        port = ScriptedPort("clarify")
        _, message, _ = await self.start(
            port, FakeSetup(SETUP), "I build CafePOS, a cafe POS in Vietnam. Compare it with competitors.")
        self.assertEqual(message.metadata["outcome"], "setup_tracking")
        self.assertIn("Using: NoteNest", message.content)

    def test_pending_prompt_frames_the_answer_with_the_question_and_keeps_the_cap(self) -> None:
        build = ConversationService._setup_prompt
        self.assertEqual(
            build(["Find my competitors please.", "It is a note app called Inkwell."], "x", {"question": "What?"}),
            "Find my competitors please.\nAsked: What?\nUser's answer: It is a note app called Inkwell.")
        self.assertEqual(build(["a", "b"], "b", None), "a\nb")
        self.assertEqual(len(build(["a" * 3000, "b"], "b", {"question": "q"})), 2000)

    async def test_model_failure_falls_back_to_the_keyword_match(self) -> None:
        _, message, _ = await self.start(
            ScriptedPort(fail=True), FakeSetup(SETUP, reply=None), "Theo dõi đối thủ giúp mình")
        self.assertEqual(message.metadata["outcome"], "setup_tracking")
        self.assertIn("Mình đã tìm được vài đối thủ", message.content)  # model reply failed: code sentence
        self.assertIn("Đang dùng: NoteNest (mặc định)", message.content)

    async def test_non_tracking_message_keeps_a_supervisor_plan(self) -> None:
        port = ScriptedPort("propose_plan")
        _, message, _ = await self.start(port, FakeSetup(SETUP), "Research local LLMs")
        self.assertEqual(message.metadata["outcome"], "propose_plan")
        self.assertNotIn("ci_setup", message.metadata)

    async def test_follow_up_answer_skips_the_supervisor_and_pending_clears_after(self) -> None:
        port, setup = ScriptedPort("setup_tracking"), FakeSetup(ASK, SETUP)
        _, asked, conversation = await self.start(port, setup, "Track my competitors")
        self.assertEqual(asked.content, "What does your product do?")  # no model reply, no "Using:" without a name
        self.assertEqual(setup.replied, [])
        self.assertEqual(conversation.metadata["ci_setup_pending"], {"question": "What does your product do?"})
        self.assertTrue(conversation.metadata["ci_setup_pending"])
        self.assertEqual(port.calls, 1)
        _, answered, conversation = await self.start(port, setup, "It is a markdown note app")
        self.assertEqual(port.calls, 1)  # no Supervisor call for the answer
        self.assertEqual(setup.prompts[1], "Track my competitors\nAsked: What does your product do?\n"
                                           "User's answer: It is a markdown note app")
        self.assertNotIn("ci_setup_pending", conversation.metadata)
        self.assertEqual(answered.metadata["outcome"], "setup_tracking")

    async def test_a_normal_question_after_a_finished_setup_goes_to_the_supervisor(self) -> None:
        port, setup = ScriptedPort("setup_tracking"), FakeSetup(SETUP)
        await self.start(port, setup, "Find my competitors")
        port.decision = "answer"
        _, message, _ = await self.start(port, setup, "What is a changelog?")
        self.assertEqual((port.calls, len(setup.prompts)), (2, 1))
        self.assertEqual(message.content, "Sure.")
        self.assertNotIn("ci_setup", message.metadata)

    async def test_setup_tracking_without_a_tracking_message_is_answered_by_the_supervisor(self) -> None:
        setup = FakeSetup(SETUP)
        _, message, _ = await self.start(ScriptedPort("setup_tracking"), setup, "What will you check on those pages?")
        self.assertEqual((message.metadata["outcome"], message.content), ("answer", "Sure."))
        self.assertEqual((setup.prompts, "ci_setup" in message.metadata), ([], False))

    async def test_a_failed_search_still_completes_the_turn(self) -> None:
        accepted, message, _ = await self.start(
            ScriptedPort("setup_tracking"), FakeSetup(RuntimeError("search down")), "Find my competitors")
        turn = (await self.service.list_turns(self.conversation.id))[-1]
        self.assertEqual(turn.status, "completed")
        self.assertIn("Search again", message.content)
        self.assertEqual(message.metadata["ci_setup"], {"product": None, "recommendation": None, "failed": True})


def product(name: str, **fields) -> MyProduct:
    return MyProduct(id=uuid4(), name=name, **fields)


class ProductResolutionTests(unittest.IsolatedAsyncioTestCase):
    def build(self, saved: list[MyProduct], brief: ProductBrief | None = None):
        self.queries: list[list[str]] = []

        def search(queries, max_results):
            self.queries.append(list(queries))
            return json.dumps({"data": {"results": [
                {"url": "https://obsidian.md/pricing", "title": "Obsidian Pricing", "snippet": "Obsidian plans"}]}})

        async def no_page(url):
            return None

        repo = MagicMock()
        repo.list = AsyncMock(return_value=saved)

        async def read_brief(prompt):
            return brief
        scout = ScoutDiscovery(search, page_fetcher=no_page, brief_reader=read_brief)
        return IntelligenceService(AsyncMock(), MagicMock(), scout=scout, my_products=repo)

    async def test_saved_product_named_in_the_request_wins_over_the_default(self) -> None:
        notenest = product("NoteNest", description="markdown notes", description_en="markdown notes app")
        other = product("Other", is_default=True, description_en="crm")
        service = self.build([other, notenest])
        result = await service.run_setup(str(uuid4()), "Track competitors of notenest")
        self.assertEqual(result["product"], {"name": "NoteNest", "description": "markdown notes",
                                             "source": "saved", "my_product_id": str(notenest.id)})
        self.assertIn("best markdown notes app", self.queries[0])

    async def test_saved_known_competitors_count_as_named_and_vn_market_searches_vietnamese(self) -> None:
        saved = product("POSly", is_default=True, description="POS cho quán cà phê",
                        description_en="cafe POS", market="vn", known_competitors=["Obsidian"])
        service = self.build([saved])
        result = await service.run_setup(str(uuid4()), "Find my competitors")
        self.assertEqual(result["product"]["source"], "default")
        self.assertEqual(self.queries[0], ["Obsidian official website"])
        self.assertEqual(result["recommendation"]["products"][0]["name"], "Obsidian")

    async def test_default_product_is_used_when_the_request_names_and_describes_nothing(self) -> None:
        saved = product("NoteNest", is_default=True, description_en="markdown notes app")
        brief = ProductBrief(own_product=None, named_competitors=[], describes_own_product=False,
                             own_product_description=None, description_in_english=None)
        result = await self.build([saved], brief).run_setup(str(uuid4()), "Find my competitors")
        self.assertEqual((result["product"]["name"], result["product"]["source"], result["product"]["my_product_id"]),
                         ("NoteNest", "default", str(saved.id)))

    async def test_unsaved_product_described_in_the_message_is_not_replaced_by_the_default(self) -> None:
        saved = product("Other", is_default=True, description_en="crm")
        brief = ProductBrief(own_product="Zed", named_competitors=[], describes_own_product=True,
                             own_product_description="a markdown app", description_in_english="a markdown app")
        result = await self.build([saved], brief).run_setup(str(uuid4()), "I make Zed, a markdown app. Find competitors")
        self.assertEqual(result["product"], {"name": "Zed", "description": "markdown app",
                                             "source": "message", "my_product_id": None})

    async def test_concurrent_setups_do_not_share_scout_state(self) -> None:
        import asyncio

        service = self.build([product("Other", is_default=True, description_en="crm")])
        first, second = await asyncio.gather(
            service.run_setup(str(uuid4()), "Find my competitors"),
            service.run_setup(str(uuid4()), "Find my competitors"))
        self.assertEqual((first["product"]["name"], second["product"]["name"]), ("Other", "Other"))
        self.assertEqual((service.scout.own_name, service.scout.used_fallback), (None, False))

    async def test_a_follow_up_answer_with_the_name_after_the_description_is_searched(self) -> None:
        prompt = "Find my competitors please.\nIt is a markdown note-taking app for Mac called Inkwell."
        result = await self.build([]).run_setup(str(uuid4()), prompt)
        self.assertEqual(result["product"]["name"], "Inkwell")
        self.assertEqual(result["product"]["description"], "markdown note-taking app for Mac")
        self.assertEqual(self.queries[0][0], "best markdown note-taking app for Mac")
        self.assertIsNone(result["recommendation"]["follow_up_question"])

    async def test_article_headings_from_the_description_and_the_own_product_are_not_competitors(self) -> None:
        article = "## 1. Vietnam\n\n## 2. Obsidian\n\n## 3. CafePOS\n"

        def search(queries, max_results):
            return json.dumps({"data": {"results": [{"url": "https://blog.example/best-cafe-pos",
                                                      "title": "Best cafe POS", "snippet": "list"}]}})

        async def page(url):
            return {"markdown": article, "links": ["https://obsidian.md/"]}

        async def brief(prompt):
            return ProductBrief(own_product="CafePOS", named_competitors=[], describes_own_product=True,
                                own_product_description="cafe management POS software in Vietnam",
                                description_in_english="cafe management POS software in Vietnam")

        service = IntelligenceService(AsyncMock(), MagicMock(), scout=ScoutDiscovery(
            search, page_fetcher=page, brief_reader=brief))
        result = await service.run_setup(str(uuid4()), "I build CafePOS, a cafe management POS software in Vietnam.")
        kinds = {(item["name"], item["kind"]) for item in result["recommendation"]["products"]}
        self.assertIn(("Obsidian", "competitor"), kinds)
        self.assertNotIn(("Vietnam", "competitor"), kinds)
        self.assertEqual([item for item in kinds if item[0] == "CafePOS"], [("CafePOS", "own")])

    async def test_no_saved_product_and_a_thin_request_asks_a_follow_up(self) -> None:
        result = await self.build([]).run_setup(str(uuid4()), "Find my competitors")
        self.assertTrue(result["recommendation"]["follow_up_question"])
        self.assertEqual(result["product"]["source"], "message")
        self.assertEqual(self.queries, [])


if __name__ == "__main__":
    unittest.main()
