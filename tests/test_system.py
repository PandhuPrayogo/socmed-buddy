from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.config
from src.db import (
    init_db,
    add_target,
    list_targets,
    insert_item,
    get_item_by_id,
    get_system_status,
    purge_expired_raw_json,
)
from src.models import CanonicalItem, Claim, Digest, DigestSection
from src.analyst import prefilter_items, validate_citations, CitationValidationError


def run_checks() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_agentic.db"

        # 1. Database schema initialization
        init_db(db_path)
        assert db_path.exists(), "Database file must be created"

        # 2. Add targets and list
        tid = add_target(db_path, "youtube", "@testchannel", external_id="UC12345")
        assert tid > 0, "Target ID must be positive integer"

        targets = list_targets(db_path)
        assert len(targets) == 1
        assert targets[0]["handle"] == "@testchannel"

        # 3. Item idempotency check (Zero duplicates)
        now = datetime.now(timezone.utc).isoformat()
        item1 = CanonicalItem(
            target_id=tid,
            platform="youtube",
            platform_item_id="vid_101",
            url="https://youtube.com/watch?v=vid_101",
            author="@testchannel",
            published_at=now,
            title="AI Educational Content",
            text="This is a comprehensive transcript regarding the real-world implementation of agentic AI systems.",
            metrics={"views": 1500, "likes": 200, "comments": 25},
            raw_json={"id": "vid_101"},
            content_hash="hash_101",
        )

        id1, is_new1 = insert_item(db_path, item1)
        assert is_new1 is True, "First insertion must report is_new=True"

        # Duplicate insertion with updated views
        item1_updated = CanonicalItem(
            target_id=tid,
            platform="youtube",
            platform_item_id="vid_101",
            url="https://youtube.com/watch?v=vid_101",
            author="@testchannel",
            published_at=now,
            title="AI Educational Content",
            text="This is a comprehensive transcript regarding the real-world implementation of agentic AI systems.",
            metrics={"views": 2500, "likes": 350, "comments": 40},
            raw_json={"id": "vid_101"},
            content_hash="hash_101",
        )
        id2, is_new2 = insert_item(db_path, item1_updated)
        assert id1 == id2, "Item ID must match on conflict"
        assert is_new2 is False, "Duplicate insertion must report is_new=False"

        # 4. Prefilter logic
        short_item = CanonicalItem(
            target_id=tid,
            platform="youtube",
            platform_item_id="vid_102",
            url="https://youtube.com/watch?v=vid_102",
            author="@testchannel",
            published_at=now,
            title="Short",
            text="Too short",
            content_hash="hash_102",
        )
        filtered, skipped = prefilter_items([item1, short_item])
        assert len(filtered) == 1
        assert skipped == 1

        # 5. Citation validation gate
        valid_digest = Digest(
            window_start=datetime.now(timezone.utc),
            window_end=datetime.now(timezone.utc),
            sections=[
                DigestSection(
                    title="AI Trends",
                    claims=[Claim(text="AI adoption is increasing rapidly across industries.", item_ids=[str(id1)])],
                )
            ],
            items_analyzed=1,
            items_skipped=0,
        )
        validate_citations(valid_digest, available_ids={str(id1)})

        # Test failure gate on invalid / hallucinated citation
        invalid_digest = Digest(
            window_start=datetime.now(timezone.utc),
            window_end=datetime.now(timezone.utc),
            sections=[
                DigestSection(
                    title="AI Trends",
                    claims=[Claim(text="Unverified claim without source.", item_ids=["999999"])],
                )
            ],
            items_analyzed=1,
            items_skipped=0,
        )
        gate_blocked = False
        try:
            validate_citations(invalid_digest, available_ids={str(id1)})
        except CitationValidationError:
            gate_blocked = True
        assert gate_blocked is True, "Citation validator must block delivery on unverifiable citations"

        # 6. Status summary
        status = get_system_status(db_path)
        assert status["active_targets"] == 1
        assert status["total_items"] == 1

        # 7. Retention purge
        purged = purge_expired_raw_json(db_path, retention_days=30)
        assert purged == 0

    print("All system self-check assertions passed successfully!")


if __name__ == "__main__":
    run_checks()
