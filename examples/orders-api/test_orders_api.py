"""The tests for the orders API example.

Four layers, each with a different question:

1. **The API tests** (`TestTheApi`): does the API enforce the business
   rules?  These are ordinary HTTP tests.
2. **The conformance test** (`TestTheConformance`): does the implementation
   obey the *spec*?  This is the generated property test, driven against the
   adapter that calls the real service.
3. **The teeth test** (`TestTheSuiteHasTeeth`): does the conformance test
   fail on the broken implementation?  A suite that stays green on a
   deliberately broken adapter proves nothing.
4. **The trace test** (`TestTheTrace`): does a recorded session conform to
   the spec?  The trace is committed, so `verispec check-trace` can replay
   it without running anything.

Run them with:

    pip install ".[api]"
    pytest -q examples/orders-api
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

pytest.importorskip("fastapi", reason="the orders API example needs fastapi")
from adapter import (  # noqa: E402
    BuggyOrdersApiAdapter,
    OrdersApiAdapter,
    OversellingOrdersApiAdapter,
)
from fastapi.testclient import TestClient  # noqa: E402
from orders_api import OrdersService, app, get_service  # noqa: E402

from verispec.model import apply_action, load_spec  # noqa: E402
from verispec.sdk import TraceRecorder  # noqa: E402
from verispec.trace import check_trace_data  # noqa: E402

SPEC_PATH = HERE / "orders.verispec.yaml"
TRACE_PATH = HERE / "traces" / "orders-session.json"


@pytest.fixture()
def spec():
    return load_spec(SPEC_PATH)


@pytest.fixture()
def client():
    get_service().reset()
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------
# 1. the API
# --------------------------------------------------------------------------


class TestTheApi:
    def test_the_catalogue_is_seeded(self, client):
        products = client.get("/products").json()
        assert [p["id"] for p in products] == [0, 1]
        assert products[0]["stock"] == 5
        assert products[1]["stock"] == 3

    def test_placing_an_order(self, client):
        response = client.post("/orders", json={"product_id": 0, "quantity": 2})
        assert response.status_code == 201, response.text
        order = response.json()
        assert order["state"] == "pending"
        assert order["amount"] == 20
        assert order["quantity"] == 2

    def test_an_unknown_product_is_a_404(self, client):
        response = client.post("/orders", json={"product_id": 9, "quantity": 1})
        assert response.status_code == 404

    def test_a_bad_quantity_is_a_422(self, client):
        assert client.post("/orders", json={"product_id": 0, "quantity": 0}).status_code == 422
        assert client.post("/orders", json={"product_id": 0, "quantity": 99}).status_code == 422

    def test_reserving_more_than_the_stock_is_a_409(self, client):
        # the first order takes all the stock of the product
        client.post("/orders", json={"product_id": 0, "quantity": 5})
        client.post("/orders/0/reserve")
        # the second order cannot be reserved: there is nothing left
        client.post("/orders", json={"product_id": 0, "quantity": 1})
        response = client.post("/orders/1/reserve")
        assert response.status_code == 409, response.text
        assert "not enough stock" in response.json()["detail"]

    def test_reserving_twice_is_a_409(self, client):
        client.post("/orders", json={"product_id": 0, "quantity": 1})
        assert client.post("/orders/0/reserve").status_code == 200
        assert client.post("/orders/0/reserve").status_code == 409

    def test_paying_twice_is_a_409(self, client):
        client.post("/orders", json={"product_id": 0, "quantity": 1})
        client.post("/orders/0/reserve")
        assert client.post("/orders/0/pay", json={"idempotency_key": "k1"}).status_code == 200
        assert client.post("/orders/0/pay", json={"idempotency_key": "k2"}).status_code == 409

    def test_reusing_an_idempotency_key_is_a_409(self, client):
        client.post("/orders", json={"product_id": 0, "quantity": 1})
        client.post("/orders", json={"product_id": 1, "quantity": 1})
        client.post("/orders/0/reserve")
        client.post("/orders/1/reserve")
        assert client.post("/orders/0/pay", json={"idempotency_key": "k1"}).status_code == 200
        assert client.post("/orders/1/pay", json={"idempotency_key": "k1"}).status_code == 409

    def test_shipping_without_paying_is_a_409(self, client):
        client.post("/orders", json={"product_id": 0, "quantity": 1})
        assert client.post("/orders/0/ship").status_code == 409

    def test_shipping_consumes_the_stock(self, client):
        client.post("/orders", json={"product_id": 0, "quantity": 2})
        client.post("/orders/0/reserve")
        client.post("/orders/0/pay", json={"idempotency_key": "k1"})
        assert client.post("/orders/0/ship").status_code == 200
        product = client.get("/products").json()[0]
        assert product["stock"] == 3  # 5 - 2
        assert product["reserved"] == 0

    def test_cancelling_a_paid_order_is_a_409(self, client):
        client.post("/orders", json={"product_id": 0, "quantity": 1})
        client.post("/orders/0/reserve")
        client.post("/orders/0/pay", json={"idempotency_key": "k1"})
        assert client.post("/orders/0/cancel").status_code == 409

    def test_cancelling_releases_the_reservation(self, client):
        client.post("/orders", json={"product_id": 0, "quantity": 2})
        client.post("/orders/0/reserve")
        product = client.get("/products").json()[0]
        assert product["reserved"] == 2
        assert client.post("/orders/0/cancel").status_code == 200
        product = client.get("/products").json()[0]
        assert product["reserved"] == 0
        assert product["stock"] == 5

    def test_an_unknown_order_is_a_404(self, client):
        assert client.get("/orders/7").status_code == 404


# --------------------------------------------------------------------------
# 2. the conformance test
# --------------------------------------------------------------------------


def _load_generated(tmp_path, name="test_generated_orders.py"):
    """Generate the conformance test from the spec and import it."""
    from verispec.gentests import gen_python

    code = gen_python(load_spec(SPEC_PATH))
    path = Path(tmp_path) / name
    path.write_text(code)
    spec = importlib.util.spec_from_file_location("generated_orders", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheConformance:
    def test_the_implementation_follows_the_spec(self, tmp_path):
        module = _load_generated(tmp_path)
        module.ADAPTER_FACTORY = OrdersApiAdapter
        module.test_verispec_conformance()

    def test_the_broken_implementation_diverges_from_the_spec(self, spec):
        """The broken implementation must not agree with the spec.

        The check is deterministic and compares the implementation with the
        spec's own effects: a random property test would only find this by
        luck.

        Note what the generated property test can and cannot catch.  It
        drives the adapter with actions the spec *allows*, so it catches an
        implementation that computes the wrong state.  It cannot catch an
        implementation that accepts an action the spec forbids: the
        generated test never takes that path.  The trace path covers that
        case, and the API's own tests cover it as well.
        """
        adapter = BuggyOrdersApiAdapter()
        adapter.reset()
        adapter.apply("place_order", order_id=0, product=0, quantity=2)

        before = adapter.snapshot()
        adapter.apply("reserve", order_id=0, product=0)
        expected = apply_action(spec, before, "reserve", {"order_id": 0, "product": 0})
        assert adapter.snapshot() == expected, "the reservation did not follow the spec"

        before = adapter.snapshot()
        adapter.apply("cancel", order_id=0)
        expected = apply_action(spec, before, "cancel", {"order_id": 0})
        assert adapter.snapshot() != expected, (
            "the broken adapter should diverge from the spec here: it keeps the reservation"
        )


# --------------------------------------------------------------------------
# 3. the trace
# --------------------------------------------------------------------------

SESSION = [
    ("place_order", {"order_id": 0, "product": 0, "quantity": 3}),
    ("reserve", {"order_id": 0, "product": 0}),
    ("pay", {"order_id": 0, "key": "k1"}),
    ("ship", {"order_id": 0}),
    ("place_order", {"order_id": 1, "product": 1, "quantity": 1}),
    ("reserve", {"order_id": 1, "product": 1}),
    ("pay", {"order_id": 1, "key": "k2"}),
]


class TestTheTrace:
    def test_a_recorded_session_conforms_to_the_spec(self, spec):
        trace = json.loads(TRACE_PATH.read_text())
        result = check_trace_data(spec, trace)
        assert result["ok"], result["detail"]

    def test_a_trace_that_oversells_is_rejected(self, spec):
        """The trace path catches what the property test cannot.

        The property test only takes actions the spec allows, so it never
        drives the implementation into the illegal path.  A trace does: when
        the implementation reserves more stock than there is, the recorded
        state violates `no_overselling` and the trace checker says so.
        """
        recorder = TraceRecorder(spec, OversellingOrdersApiAdapter(OrdersService()))
        recorder.reset()
        recorder.apply("place_order", order_id=0, product=0, quantity=4)
        recorder.apply("reserve", order_id=0, product=0)
        recorder.apply("place_order", order_id=1, product=0, quantity=4)
        recorder.apply("reserve", order_id=1, product=0)

        result = check_trace_data(spec, recorder.steps)
        assert not result["ok"]
        assert "no_overselling" in result["violated"]
        assert result["failed_step"] == 4

    @pytest.mark.parametrize("language", ["typescript", "go"])
    def test_the_other_implementations_record_the_same_session(self, spec, language):
        """One spec, three implementations, the same behaviour.

        The Python, TypeScript and Go implementations record the same session.
        Comparing the traces is the differential oracle: if two
        implementations of the same contract drift apart, the traces say so.
        """
        from verispec.oracle import diff_traces

        other = HERE / "traces" / f"orders-session-{language}.json"
        if not other.exists():
            pytest.skip(f"the {language} implementation has not recorded its trace yet")
        python = json.loads(TRACE_PATH.read_text())
        result = diff_traces(python, json.loads(other.read_text()))
        assert result["identical"], result["divergence"]

    def test_the_committed_trace_is_up_to_date(self, spec, tmp_path):
        """Regenerate the trace and compare it with the committed one."""
        recorder = TraceRecorder(spec, OrdersApiAdapter(OrdersService()))
        recorder.reset()
        for action, params in SESSION:
            recorder.apply(action, **params)
        assert json.loads(TRACE_PATH.read_text()) == recorder.steps
