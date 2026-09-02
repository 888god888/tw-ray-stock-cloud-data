# -*- coding: utf-8 -*-
"""Update official Taiwan stock data and publish a mobile cloud snapshot."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from data_store import DataStore
from mobile_export import build_mobile_snapshot, write_mobile_snapshot
from official_data import OfficialDataService


def _progress(message, done, total):
    print(f"[{done:>4}/{total:<4}] {message}", flush=True)


def _gzip_file(source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with source.open("rb") as reader, temporary.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
    temporary.replace(destination)


def _sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _market_rows(store: DataStore):
    candidates = store.get_candidates(min_bars=180, max_stocks=0)
    rows = []
    for row in candidates.itertuples(index=False):
        rows.append({
            "stock_id": str(row.stock_id),
            "name": str(row.stock_name),
            "industry": str(row.industry_category or "未分類"),
            "capital_billion": (
                float(row.paid_in_capital) / 100_000_000
                if row.paid_in_capital is not None else None
            ),
            "close": float(row.close or 0),
            "change_pct": 0,
            "volume": float(row.Trading_Volume or 0) / 1000,
            "match": "—",
        })
    return rows


def run(db_path: Path, output_dir: Path, force_initial=False):
    output_dir.mkdir(parents=True, exist_ok=True)
    store = DataStore(db_path)
    service = OfficialDataService(store)
    coverage = store.coverage_summary()
    needs_initial = force_initial or coverage.get("price_rows", 0) < 10_000
    if needs_initial:
        print("Starting full official-data bootstrap", flush=True)
        sync_result = service.initialise(
            callback=_progress,
            price_calendar_days=400,
            revenue_months=30,
            financial_quarters=16,
            institutional_calendar_days=230,
        )
        financial_backfill = sync_result.get("financial", {})
    else:
        print("Starting incremental official-data update", flush=True)
        sync_result = service.incremental_update(callback=_progress)
        financial_backfill = {}
        migration_key = "migration:financial-health-v1"
        if not store.sync_succeeded(migration_key):
            print("Backfilling 16 quarters of complete financial statements", flush=True)
            financial_backfill = service.sync_financial_history(
                quarters=16, callback=_progress
            )
            sync_result["financial_backfill"] = financial_backfill

    # Existing cloud databases already have price history, so they do not enter
    # the full bootstrap branch. This marker makes V9 backfill all statements
    # once, then return to fast daily incremental updates.
    if financial_backfill and not financial_backfill.get("errors"):
        store.set_sync_state(
            "migration:financial-health-v1", "ok",
            int(financial_backfill.get("rows", 0))
            + int(financial_backfill.get("derived_rows", 0)),
            "16 季損益、資產負債、現金流量表已完成",
        )

    rows = _market_rows(store)
    if not rows:
        raise RuntimeError("No stocks with at least 180 daily bars; keeping previous release")

    snapshot = build_mobile_snapshot(
        store, rows, conditions=[], price_days=260, revenue_months=30,
        financial_quarters=16, institutional_days=120,
        progress_callback=lambda done, total, stock_id: (
            print(f"Export {done}/{total}: {stock_id}", flush=True)
            if done == 1 or done == total or done % 100 == 0 else None
        ),
    )
    snapshot.update({
        "schema_version": 3,
        "strategy_name": "雲端完整市場資料（由手機設定條件）",
        "sync_mode": "initial" if needs_initial else "incremental",
    })
    if snapshot.get("financial_analysis_error_count"):
        print(
            "WARNING: financial analysis skipped for "
            f"{snapshot['financial_analysis_error_count']} stocks",
            flush=True,
        )
    if snapshot.get("chip_analysis_error_count"):
        print(
            "WARNING: chip analysis skipped for "
            f"{snapshot['chip_analysis_error_count']} stocks",
            flush=True,
        )
    json_path = write_mobile_snapshot(output_dir / "snapshot.json", snapshot)
    gzip_path = output_dir / "snapshot.json.gz"
    _gzip_file(json_path, gzip_path)

    db_gzip_path = output_dir / "tw_stock_cloud.sqlite3.gz"
    _gzip_file(db_path, db_gzip_path)
    manifest = {
        "schema_version": 2,
        "generated_at": snapshot["generated_at"],
        "latest_trade_date": snapshot["latest_trade_date"],
        "stock_count": snapshot["stock_count"],
        "snapshot_file": gzip_path.name,
        "snapshot_sha256": _sha256(gzip_path),
        "snapshot_size": gzip_path.stat().st_size,
        "database_file": db_gzip_path.name,
        "database_sha256": _sha256(db_gzip_path),
        "database_size": db_gzip_path.stat().st_size,
        "coverage": store.coverage_summary(),
        "sync_result": sync_result,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({
        "latest_trade_date": snapshot["latest_trade_date"],
        "stock_count": snapshot["stock_count"],
        "snapshot_size": gzip_path.stat().st_size,
    }, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/tw_stock_cloud.sqlite3")
    parser.add_argument("--output", default="release")
    parser.add_argument("--force-initial", action="store_true")
    args = parser.parse_args()
    run(Path(args.db).resolve(), Path(args.output).resolve(), args.force_initial)


if __name__ == "__main__":
    main()
