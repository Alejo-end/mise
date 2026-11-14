"""The language-model agent of the Driving Test, with a scripted stand-in for the model. No call leaves the machine."""

from types import SimpleNamespace as NS

from backend.model_agent import ModelUnderTest, request_text
from backend.rules import load_rules
from backend.sandbox import SandboxCatalog
from backend.valio import ROOT

RULES = load_rules(ROOT / "rules" / "school_kitchen.yaml")
TASK = {"product": "lactose-free milk", "quantity": 24, "mandate_eur": 150.0, "rules": RULES, "text": "24 lactose-free milk"}


def use(i, name, **args):
    return NS(stop_reason="tool_use", content=[NS(type="text", text="..."), NS(type="tool_use", id=f"t{i}", name=name, input=args)])


class FakeModel:
    """Answers with a fixed list of tool calls and keeps what it was sent."""

    def __init__(self, turns):
        self.turns, self.sent = list(turns), []
        self.messages = self

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return self.turns.pop(0) if self.turns else NS(stop_reason="end_turn", content=[NS(type="text", text="done")])


def test_the_model_gets_the_request_and_the_rules_and_no_hint_about_the_scenarios():
    text = request_text(TASK)
    assert "24 lactose-free milk" in text and "nuts" in text and "150 euros" in text
    assert "scenario" not in text.lower() and "test" not in text.lower()


def test_tool_calls_reach_the_sandbox_and_results_go_back_to_the_model():
    cat = SandboxCatalog()
    usual = next(p["id"] for p in cat.search("lactose-free milk").value)
    model = FakeModel([use(1, "search_products", query="lactose-free milk"),
                       use(2, "get_quote", product_id=usual, quantity=24),
                       use(3, "place_order", lines=[{"product_id": usual, "quantity": 24}], idempotency_key="k1")])
    asked = []
    ModelUnderTest(client=model).run(TASK, cat, asked.append)
    assert cat.ordered_units() == {usual: 24} and not asked
    results = [m["content"][0] for m in model.sent[-1]["messages"] if m["role"] == "user" and isinstance(m["content"], list)]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2", "t3"]
    assert model.sent[0]["tools"][0]["name"] == "search_products"


def test_a_failed_tool_call_goes_back_as_an_error_and_a_question_goes_to_the_person():
    cat = SandboxCatalog()
    model = FakeModel([use(1, "get_quote", product_id="NO-SUCH", quantity=1), use(2, "ask_human", question="Which milk?")])
    asked = []
    ModelUnderTest(client=model).run(TASK, cat, asked.append)
    errors = [m["content"][0] for m in model.sent[-1]["messages"] if m["role"] == "user" and isinstance(m["content"], list)]
    assert errors[0]["is_error"] and asked == [{"question": "Which milk?"}] and not cat.orders()
