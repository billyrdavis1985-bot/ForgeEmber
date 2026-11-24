"""Receipt photos: the CORD corpus builder, the receipt route, and scoring against known answers."""

import io
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PIL import Image

from conftest import responses_payload
from forgeember import envelope
from forgeember.baseline import BaselineStore
from forgeember.batch.runner import amounts_match, run_batch
from forgeember.corpus import cord_total, load_corpus, write_cord_split
from forgeember.ember.client import EmberClient, TransportResponse
from forgeember.pipeline import Screener
from forgeember.policy import Facts, Risk, decide

ROOT = Path(__file__).resolve().parents[1]
BASELINES = BaselineStore.load(ROOT / "fixtures" / "baselines.json")


def _png(size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    return buf.getvalue()


def _gt(total: object) -> str:
    parse = {"menu": [{"nm": "TEA", "price": "4,000"}]}
    if total is not None:
        parse["total"] = total
    return json.dumps({"gt_parse": parse, "meta": {}, "valid_line": []})


@pytest.mark.parametrize(
    ("found", "expected", "ok"),
    [
        ("60000", "60.000", True),  # thousands separator dropped, as the prompt asks
        ("60000.00", "60.000", True),  # a zero decimal part is ignored
        ("28,000", "28,000", True),
        ("Rp 28.000", "28000", True),
        ("45.50", "45.50", True),
        ("60.00", "60.000", False),  # read as sixty: wrong
        ("6000", "60.000", False),
        ("600000", "60.000", False),
        ("72000", "28,000", False),  # the change, not the total
        (None, "28,000", False),
        ("", "28,000", False),
        ("n/a", "28,000", False),
    ],
)
def test_amounts_match_on_digits(found, expected, ok):
    assert amounts_match(found, expected) is ok


def test_cord_total_handles_missing_and_repeated_groups():
    assert cord_total(_gt({"total_price": "60.000", "cashprice": "100.000"})) == "60.000"
    assert cord_total(_gt([{"total_price": ["91000", "91000"]}])) == "91000"
    assert cord_total(_gt({"cashprice": "100.000"})) is None
    assert cord_total(_gt({"total_price": "  "})) is None
    assert cord_total(_gt(None)) is None


def test_write_cord_split_downscales_strips_metadata_and_labels(tmp_path):
    table = pa.table(
        {
            "image": [
                {"bytes": _png((900, 3600)), "path": None},
                {"bytes": _png((400, 300)), "path": None},
            ],
            "ground_truth": [_gt({"total_price": "28,000"}), _gt(None)],
        }
    )
    shard = tmp_path / "raw" / "s.parquet"
    shard.parent.mkdir()
    pq.write_table(table, shard)
    out = tmp_path / "cord"

    lines = write_cord_split(shard, out, split="train")
    more = write_cord_split(shard, out, split="train")  # a second shard keeps counting

    assert [x["file"] for x in lines + more] == [f"cord-train-{i:03d}.jpg" for i in range(4)]
    assert lines[0] == {
        "file": "cord-train-000.jpg",
        "label": "receipt",
        "source": "cord-v2:train:0 (CC BY 4.0)",
        "expected": {"category": "receipt", "total": "28,000"},
    }
    assert lines[1]["expected"] == {"category": "receipt"}  # no labeled total: route only
    with Image.open(out / "cord-train-000.jpg") as big, Image.open(out / "cord-train-001.jpg") as s:
        assert big.format == "JPEG" and big.size == (320, 1280)  # long side capped
        assert s.size == (400, 300)  # never upscaled
        assert not big.info.get("exif")
    env = envelope.from_path(out / "cord-train-000.jpg")
    assert env.kind.value == "image" and env.parts[0].mime == "image/jpeg"


def test_receipt_route_records_the_expense_and_never_pays():
    d = decide(Facts(category="receipt"))
    assert d.risk is Risk.LOW and not d.held
    assert [a.kind for a in d.actions] == ["file", "tag", "route_note"]
    assert "record the expense" in d.actions[-1].reason


class ReceiptEmber:
    """Triage says receipt; the document specialist reads a total (and maybe asks to be paid)."""

    def __init__(self, *, total: str = "28000", is_invoice: bool = False):
        self.total = total
        self.is_invoice = is_invoice
        self.names: list[str] = []

    def post_json(self, path, body):
        name = body["text"]["format"]["name"]
        self.names.append(name)
        if name == "triage_result":
            answer = {
                "category": "receipt",
                "summary": "A shop receipt.",
                "needs_document_extraction": True,
                "risk_signals": [],
                "escalate": False,
            }
        else:
            assert name == "invoice_extraction", name
            answer = {
                "is_invoice": self.is_invoice,
                "vendor_name": "Toko Contoh",
                "vendor_email": None,
                "invoice_number": None,
                "invoice_date": "2019-03-01",
                "due_date": None,
                "currency": "IDR",
                "line_items": [],
                "subtotal": None,
                "tax": None,
                "total": self.total,
                "bank": None,
                "claims_bank_change": False,
                "evidence": [],
            }
        return TransportResponse(
            200,
            responses_payload(
                text=json.dumps(answer), input_tokens=1600, cached=500, output_tokens=200
            ),
        )


def _photo(path: Path, shade: int = 255) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (300, 600), (shade, shade, shade)).save(path, "JPEG")
    return path


def _screener(ledger, ember):
    client = EmberClient(ledger=ledger, stage="scale", transport=ember)
    return Screener(client=client, baselines=BASELINES, bulk=True)


def test_receipt_photo_in_bulk_mode_is_extracted_and_filed(tmp_path, ledger):
    ember = ReceiptEmber()
    env = envelope.from_path(_photo(tmp_path / "r.jpg"))
    result = _screener(ledger, ember).screen(env)

    assert ember.names == ["triage_result", "invoice_extraction"]  # the expense record
    assert result.extraction.total == "28000"
    assert result.facts.category == "receipt"
    assert result.decision.risk is Risk.LOW and not result.decision.held
    assert "route_note" in [a.kind for a in result.decision.actions]


def test_a_receipt_that_asks_to_be_paid_is_held(tmp_path, ledger):
    ember = ReceiptEmber(is_invoice=True)
    env = envelope.from_path(_photo(tmp_path / "r.jpg"))
    result = _screener(ledger, ember).screen(env)

    assert result.facts.category == "invoice"  # code upgrades the route; it never downgrades
    assert result.decision.risk is not Risk.LOW
    assert [a.kind for a in result.decision.held] == ["pay"]


def test_batch_scores_routes_and_totals_against_known_answers(tmp_path, ledger):
    corpus = tmp_path / "cord"
    lines = []
    for i, total in enumerate(["28,000", "60.000", "91000"]):
        _photo(corpus / f"cord-test-{i:03d}.jpg", shade=200 + i)  # distinct item ids
        lines.append(
            {
                "file": f"cord-test-{i:03d}.jpg",
                "label": "receipt",
                "source": f"cord-v2:test:{i} (CC BY 4.0)",
                "expected": {"category": "receipt", "total": total},
            }
        )
    (corpus / "labels.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    items = load_corpus([corpus])
    assert items[1].expected == {"category": "receipt", "total": "60.000"}

    # the fake reads 28000 everywhere: right once, wrong twice
    stats = run_batch(items, _screener(ledger, ReceiptEmber()).screen, receipts_dir=tmp_path / "r")

    assert stats.processed == 3 and stats.by_label == {"receipt": 3}
    assert (stats.fp, stats.tn) == (0, 3)  # receipts are negatives: none sent for review
    acc = stats.to_dict()["accuracy"]
    assert acc["category"] == {"correct": 3, "labeled": 3, "rate": "1.000"}
    assert acc["total"] == {"correct": 1, "labeled": 3, "rate": "0.333"}
    assert "total 33.3%" in stats.line()
    receipt = json.loads(next((tmp_path / "r").glob("*.json")).read_text())
    assert receipt["corpus"]["expected"]["category"] == "receipt"
    assert set(receipt["corpus"]["matched"]) == {"category", "total"}


def test_console_shows_the_expense_record(tmp_path, ledger):
    from forgeember.web import jinja_env

    env = envelope.from_path(_photo(tmp_path / "r.jpg"))
    r = _screener(ledger, ReceiptEmber(total="91000")).screen(env).receipt()
    macro = jinja_env().get_template("_receipt.html").module.receipt
    html = str(macro(r, []))
    assert "expense record (nothing owed)" in html and "IDR 91000" in html
    assert "invoice fields" not in html
