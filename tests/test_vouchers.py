"""Voucher codes: formatting, batches, voiding."""

from __future__ import annotations

from app.services import catalog, vouchers


def test_codes_are_normalised_for_typing(client):
    assert vouchers.normalize_code("abcd efgh") == "ABCD-EFGH"
    assert vouchers.normalize_code("abcd-efgh") == "ABCD-EFGH"
    assert vouchers.normalize_code("  abcdefgh  ") == "ABCD-EFGH"
    assert vouchers.normalize_code("abcd") == "ABCD"
    assert vouchers.normalize_code("abcdefghij") == "ABCD-EFGH"
    assert vouchers.normalize_code("") == ""


def test_generated_codes_avoid_ambiguous_characters(client):
    plan = catalog.list_plans()[0]
    _, codes = vouchers.create_batch(plan=plan, quantity=25)

    assert len(set(codes)) == 25
    for code in codes:
        assert len(code) == 9 and code[4] == "-"
        assert not set(code) & set("01OIL")


def test_batch_tracks_unused_and_sold_counts(client):
    plan = catalog.list_plans()[0]
    batch_id, codes = vouchers.create_batch(plan=plan, quantity=4, label="Counter")

    vouchers.void_voucher(vouchers.get_by_code(codes[0])["id"])

    batch = next(row for row in vouchers.list_batches() if row["id"] == batch_id)
    assert batch["quantity"] == 4
    assert batch["unused_count"] == 3
    assert batch["sold_count"] == 0
    assert vouchers.counts() == {"unused": 3, "active": 0, "used": 0, "expired": 0, "void": 1}


def test_voiding_a_whole_batch(client):
    plan = catalog.list_plans()[0]
    batch_id, _ = vouchers.create_batch(plan=plan, quantity=5)

    assert vouchers.void_batch(batch_id) == 5
    assert vouchers.void_batch(batch_id) == 0
    assert vouchers.counts()["unused"] == 0
    assert vouchers.counts()["void"] == 5


def test_lookup_is_case_and_separator_insensitive(client):
    plan = catalog.list_plans()[0]
    _, codes = vouchers.create_batch(plan=plan, quantity=1)
    messy = codes[0].replace("-", " ").lower()

    assert vouchers.get_by_code(messy)["code"] == codes[0]
