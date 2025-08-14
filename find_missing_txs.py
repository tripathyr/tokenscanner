#!/usr/bin/env python3
import argparse
import sys
import time
from typing import Iterable, List, Set, Tuple, Dict, Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timezone
import csv

DEFAULT_PAGESIZE = 1000
TIMEOUT = 20

def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": "addr-compare/1.1"})
    return s

def join_addr_url(base: str, address: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/api"):
        return f"{base}/address/{address}"
    return f"{base}/api/address/{address}"

def join_tx_url(base: str, txid: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/api"):
        return f"{base}/tx/{txid}"
    return f"{base}/api/tx/{txid}"

def extract_txids_from_page(payload: Dict[str, Any]) -> List[str]:
    txids: List[str] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("txs"), list):
            for tx in payload["txs"]:
                tid = (tx.get("txid") if isinstance(tx, dict) else None)
                if isinstance(tid, str):
                    txids.append(tid)
        elif isinstance(payload.get("transactions"), list):
            for tx in payload["transactions"]:
                tid = (tx.get("txid") if isinstance(tx, dict) else None)
                if isinstance(tid, str):
                    txids.append(tid)
        elif isinstance(payload.get("txids"), list):
            for tid in payload["txids"]:
                if isinstance(tid, str):
                    txids.append(tid)
    return txids

def fetch_all_txids(session: requests.Session, base: str, address: str,
                    page_size: int = DEFAULT_PAGESIZE) -> Tuple[Set[str], int, float, float]:
    """
    Returns (txids_set, tx_count_reported, total_received, total_sent).
    """
    url = join_addr_url(base, address)
    txids: Set[str] = set()
    page = 1
    last_len = None

    reported_count = None
    total_received = None
    total_sent = None

    while True:
        params = {"page": page, "pageSize": page_size}
        resp = session.get(url, params=params, timeout=TIMEOUT)
        if not resp.ok:
            raise RuntimeError(f"GET {resp.url} -> {resp.status_code} {resp.text[:200]}")

        data = resp.json()

        if isinstance(data, dict):
            if reported_count is None:
                reported_count = (data.get("txApperances") or
                                  data.get("txAppearances") or
                                  data.get("tx_count") or
                                  (len(data.get("transactions")) if isinstance(data.get("transactions"), list) else None))
                total_received = data.get("totalReceived", total_received)
                total_sent = data.get("totalSent", total_sent)

        page_txids = extract_txids_from_page(data)

        if page == 1 and page_txids and len(page_txids) < page_size:
            txids.update(page_txids)
            break

        if not page_txids:
            break

        before = len(txids)
        txids.update(page_txids)
        after = len(txids)
        if after == before:
            break

        if last_len is not None and len(page_txids) < page_size:
            break

        last_len = len(page_txids)
        page += 1
        time.sleep(0.03)

    return txids, (reported_count or len(txids)), float(total_received or 0.0), float(total_sent or 0.0)

def safe_int(x) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None

def fetch_tx_meta(session: requests.Session, base: str, txid: str) -> Dict[str, Any]:
    """
    Fetch tx details and extract minimal metadata.
    Expected fields (robust to naming): blockheight/blockHeight, time, confirmations.
    """
    url = join_tx_url(base, txid)
    resp = session.get(url, timeout=TIMEOUT)
    meta = {"source": base, "txid": txid, "blockheight": None, "time_unix": None, "time_iso": None, "confirmations": None}
    if not resp.ok:
        meta["error"] = f"{resp.status_code}"
        return meta

    try:
        j = resp.json()
    except Exception as e:
        meta["error"] = f"json: {e}"
        return meta

    if isinstance(j, dict):
        bh = j.get("blockheight", j.get("blockHeight"))
        tm = j.get("time")  # usually UNIX seconds
        conf = j.get("confirmations")

        bh = safe_int(bh)
        tm = safe_int(tm)
        conf = safe_int(conf)

        meta["blockheight"] = bh
        meta["time_unix"] = tm
        if tm is not None:
            meta["time_iso"] = datetime.fromtimestamp(tm, tz=timezone.utc).isoformat()
        meta["confirmations"] = conf

    return meta

def main():
    ap = argparse.ArgumentParser(
        description="Compare FLO address transactions between Blockbook and Indexer, list missing txids with metadata."
    )
    ap.add_argument("--blockbook", required=True, help="Blockbook base URL, e.g. https://blockbook.flocard.app")
    ap.add_argument("--indexer",   required=True, help="Indexer base URL, e.g. https://blockbook.ranchimall.net")
    ap.add_argument("--address",   required=True, help="FLO address to check")
    ap.add_argument("--pagesize",  type=int, default=DEFAULT_PAGESIZE, help=f"Page size (default: {DEFAULT_PAGESIZE})")
    ap.add_argument("--csv",       required=True, help="Write CSV of differences to this path")
    args = ap.parse_args()

    sess = make_session()

    print(f"Fetching from Blockbook …", file=sys.stderr)
    bb_txids, bb_count_reported, bb_recv, bb_sent = fetch_all_txids(sess, args.blockbook, args.address, args.pagesize)

    print(f"Fetching from Indexer …", file=sys.stderr)
    ix_txids, ix_count_reported, ix_recv, ix_sent = fetch_all_txids(sess, args.indexer, args.address, args.pagesize)

    only_in_blockbook = sorted(bb_txids - ix_txids)
    only_in_indexer   = sorted(ix_txids - bb_txids)

    print("\n=== Summary ===")
    print(f"Address:                 {args.address}")
    print(f"Blockbook txids:         {len(bb_txids)} (reported: {bb_count_reported})")
    print(f"Indexer txids:           {len(ix_txids)} (reported: {ix_count_reported})")
    print(f"Blockbook totals:        received={bb_recv}  sent={bb_sent}")
    print(f"Indexer totals:          received={ix_recv}  sent={ix_sent}")
    print(f"Missing in Indexer:      {len(only_in_blockbook)}")
    print(f"Missing in Blockbook:    {len(only_in_indexer)}")

    rows = []
    # Fetch metadata for missing txs from the side where they exist
    print("\nFetching metadata for missing-in-Indexer …", file=sys.stderr)
    for t in only_in_blockbook:
        meta = fetch_tx_meta(sess, args.blockbook, t)
        rows.append({
            "category": "only_in_blockbook", "txid": t,
            "source": args.blockbook,
            "blockheight": meta.get("blockheight"),
            "time_unix": meta.get("time_unix"),
            "time_iso": meta.get("time_iso"),
            "confirmations": meta.get("confirmations"),
            "error": meta.get("error", "")
        })
        time.sleep(0.01)

    print("Fetching metadata for missing-in-Blockbook …", file=sys.stderr)
    for t in only_in_indexer:
        meta = fetch_tx_meta(sess, args.indexer, t)
        rows.append({
            "category": "only_in_indexer", "txid": t,
            "source": args.indexer,
            "blockheight": meta.get("blockheight"),
            "time_unix": meta.get("time_unix"),
            "time_iso": meta.get("time_iso"),
            "confirmations": meta.get("confirmations"),
            "error": meta.get("error", "")
        })
        time.sleep(0.01)

    # Write CSV
    with open(args.csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "category", "txid", "source", "blockheight", "time_unix", "time_iso", "confirmations", "error"
        ])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"\nWrote CSV diff to {args.csv}")

if __name__ == "__main__":
    main()
