"""Oregon ORS publisher HTML, without consolidating later session laws.

The fixed 689-link seed is the anonymous public ORS.aspx volume/title UI observed
2026-09-07 UTC. Administrative SharePoint endpoints returned 401 and are not used.
Refresh the seed through the public UI when the edition changes; no URL scanning.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from lxml import etree, html

from .acquire import Acquirer, AcquisitionError
from .parse import markup, media_links, readable, source_links
from .store import Provision, Store

BASE = "https://www.oregonlegislature.gov"
INDEX = BASE + "/bills_laws/Pages/ORS.aspx"
DISCLAIMER = BASE + "/Pages/disclaimer.aspx"
UPDATE = BASE + "/lc/Pages/ORSupdate.aspx"
COLLECTION = "oregon-ors"
WARNING = (
    "2025 edition excludes 2025 special-session and 2026 regular-session changes. "
    "Online text is not the legally official printed text. Later enactments are not consolidated."
)
# Every token below was an actual href suffix in the fully expanded publisher UI.
SEED_GROUPS = """
001 002 003 004 005 007 008 009 010
011 012 013 014 015 016 017 018 019 020 021 022 023 024 025
026 027 028 029 030 031 032 033 034 035 036 037
040 041 042 043 044 045
046
051 052 053 054 055
056 057 058 059 060 061 062 063 064 065 067 068 069 070
071 072 072A 073 074 074A 075 076 077 078 079 080 081 082 083 084 079A
086 086A 087 088
090 091 092 093 094 095 096 097 098 099 100 101 105
106 107 108 109 110
111 112 113 114 115 116 117 118 119 120 121
124 125 126 127 128 129 130
131 131A 132 133 134 135 136 137 138 139 140 141 142 143 144 145 146 147 148 149 151 153 156 157
161 162 163 163A 164 165 166 167 168 169
171 172 173 174
176 177 178 179 180 181 181A 182 183 184 185
186 187 188 189 190 191 192 193 194 195 196 197 197A 198 199 200
201 202 203 204 205 206 207 208 209 210 214 215
221 222 223 224 225 226 227
236 237 238 238A 239 240 241 242 243 244
246 247 248 249 250 251 252 253 254 255 258 259 260
261 262 263 264 265 266 267 268
270 271 272 273 274 275
276 276A 278 279 279A 279B 279C 280 281 282 283
284 285 285A 285B 285C
286 286A 287 287A 288 289
291 292 293 294 295 297
305 306 307 308 308A 309 310 311 312 314 315 316 317 317A 318 319 320 321 323 324
326 327 328 329 329A 330 331 332 333 334 335 336 337 338 339 340 341 342 343 344 345 346 348 350 351 352 353 354 357 358 359 360
366 367 368 369 370 371 372 373 374 375 376 377 381 382 383 384 385 390 391
396 397 398 399 401 402 403 404
406 407 408
409 410 411 412 413 414 415 416 417 418 419 419A 419B 419C 420 420A 421 423
426 427 428 430
431 431A 432 433 434 435 436 437 438 440 441 442 443 444 445 446 447 448 449 450 451 452 453 454
455 456 457 458 459 459A 460 461 462 463 464 465 466 467 468 468A 468B 469 469A 469B 470
471 472 473 474 475 475A 475B 475C
476 477 478 479 480 481 482 483 484 485 486 487 488 491 492 493 494
496 497 498 501
506 507 508 509 511 513
516 517 520 522 523
526 527 528 530 532
536 537 538 539 540 541 542 543 543A 544 545 547 548 549 550 551 552 553 554 555 558
561 562 563 564 565 566 567 568 569 570 571 572 573
576 577 578 579 580 582 583 584 585 586 587
596 597 598 599 600 601 602 603 604 605 606 607 608 609 610
616 618 619 620 621 622 624 625 626 627 628 632 633 634 635
645 646 646A 647 648 649 650
651 652 653 654 655 656 657 657A 657B 658 659 659A 660 661 662 663
670 671 672 673 674 675 676 677 678 679 680 681 682 683 684 685 686 687 688 689 690 691 692 693 694 695 696 697 698 699 700 701 702 703 704
705
706 707 708 708A 709 711 713 714 715 716 717
721 722 723 724 725 725A 726 727
731 732 733 734 735 736 737 738 739 740 741 742 743 743A 743B 744 745 746 747 748 749 750 751 752
756 757 758 759 760 761 763 764 767 768 769 770 771 772 773 774
776 777 778 780 781 782 783
801 802 803 805 806 807 809 810 811 813 814 815 816 818 819 820 821 822 823 824 825 826
830
835 836 837 838
"""
CHAPTERS = tuple(SEED_GROUPS.split())
PRIORITY = set(
    "090 091 092 093 094 095 096 097 098 099 100 101 105 195 196 197 197A 215 227 455 456 457 458 459 459A 460 461 462 463 464 465 466 467 468 468A 468B 469 469A 469B 470 526 527 528 530 532 536 537 538 539 540 541 542 543 543A 544 545 547 548 549 550 551 552 553 554 555 558".split()
)


def chapter_url(chapter: str) -> str:
    return BASE + "/bills_laws/ors/ors" + chapter + ".html"


def chapter_units(
    data: bytes, chapter: str, url: str
) -> tuple[str, list[Provision], dict[str, Any]]:
    # Publisher Word exports use Windows-1252 without a charset declaration.
    # libxml's HTML decoder preserves those bytes as the corresponding punctuation.
    page = html.fromstring(data)
    containers = page.xpath('//div[starts-with(@class,"WordSection")]')
    if not containers:
        raise ValueError("Unrecognized ORS chapter structure")
    nodes = [child for container in containers for child in container]
    literal_chapter = chapter.lstrip("0")
    title_node = next(
        (
            n
            for n in nodes
            if re.match(r"Chapter\s+" + re.escape(literal_chapter) + r"\b", readable(n))
        ),
        None,
    )
    if title_node is None:
        raise ValueError("Chapter identity not verified in publisher text")
    title = " ".join(readable(title_node).split())
    number = re.compile(r"^(" + re.escape(literal_chapter) + r"\.\d{3,5}[A-Za-z]?)\b")
    starts: list[tuple[int, str, str]] = []
    for i, node in enumerate(nodes):
        text = readable(node)
        match = number.match(text)
        if not match or node.tag != "p":
            continue
        bold = " ".join(" ".join(node.xpath(".//b//text()|.//strong//text()")).split())
        if number.match(bold) or (starts and re.match(number.pattern + r"\s*\[", text)):
            starts.append((i, match[1], bold or text))
    blocks = [(0, "front-matter", title)] + starts
    counts = Counter(key for _, key, _ in blocks)
    seen: Counter[str] = Counter()
    units = []
    for block_no, (start, key, heading) in enumerate(blocks):
        end = blocks[block_no + 1][0] if block_no + 1 < len(blocks) else len(nodes)
        wrapper = etree.Element("div", attrib={"data-source-chapter": literal_chapter})
        for node in nodes[start:end]:
            wrapper.append(deepcopy(node))
        text = readable(wrapper)
        if not text:
            continue
        seen[key] += 1
        native = literal_chapter if key == "front-matter" else key
        suffix = f":occurrence-{seen[key]}" if counts[key] > 1 else ""
        units.append(
            Provision(
                key=(
                    f"ors:{literal_chapter}:front-matter"
                    if key == "front-matter"
                    else "ors:" + key + suffix
                ),
                citation=f"ORS Chapter {literal_chapter}"
                if key == "front-matter"
                else "ORS " + key,
                heading=heading,
                text=text,
                markup=markup(wrapper),
                url=url,
                parent_key=None if key == "front-matter" else f"ors:chapter:{literal_chapter}",
                unit_kind="chapter_front_matter" if key == "front-matter" else "section",
                metadata={
                    "native_identifier": native,
                    "duplicate_identifier_count": counts[key],
                    "occurrence": seen[key],
                    "tables": len(wrapper.xpath(".//table")),
                    "media": media_links(wrapper, url),
                    "warning": WARNING,
                    "boundary_semantics": "Source paragraphs through next numbered heading; intervening notes and headings retained.",
                },
                references=source_links(wrapper, url),
            )
        )
    editions = sorted(set(re.findall(r"\b(20\d{2})\s+EDITION\b", readable(page))))
    return (
        title,
        units,
        {
            "page_edition_labels": editions,
            "warning": WARNING,
            "source_chapter": literal_chapter,
            "sections": len(starts),
            "full_text_preserved": True,
            "media": media_links(page, url),
        },
    )


def sync_oregon(s: Store, a: Acquirer, limit: int | None = None, as_of: str | None = None) -> None:
    if as_of:
        raise ValueError(
            "ORS is a 2025-edition observation, not a point-in-time legal reconstruction"
        )
    if a.delay < 1:
        raise ValueError("Oregon acquisition requires at least one second per publisher host")
    policy = a.fetch(DISCLAIMER)
    index = a.fetch(INDEX)
    update = a.fetch(UPDATE)
    index_page = html.fromstring(s.artifact(index.sha256))
    index_text = " ".join(index_page.text_content().split())
    if "2025 Edition does not include" not in index_text:
        raise ValueError(
            "Publisher edition changed; reverify public chapter inventory before collection"
        )
    volume_counts = [
        int(m[1])
        for node in index_page.xpath("//tbody[@groupstring]")
        if "Volume" in readable(node)
        for m in [re.search(r"\((\d+)\)\s*$", readable(node))]
        if m
    ]
    if sum(volume_counts) != len(CHAPTERS):
        raise ValueError(
            "Publisher inventory count changed; refresh actual public chapter href seed"
        )
    s.collection(
        COLLECTION,
        ("us-or", "Oregon", "state", "us"),
        name="Oregon Revised Statutes — 2025 edition",
        authority="Oregon Legislative Counsel / Oregon Legislative Assembly",
        kind="statutory_code",
        homepage=INDEX,
        source_status="Publisher online compilation; official text is printed publication",
        access="Anonymous public HTML; robots checked. Public-service disclaimer; no affirmative reuse license inferred.",
        metadata={
            "warning": WARNING,
            "edition": "2025",
            "index_artifact": index.sha256,
            "policy_artifact": policy.sha256,
            "update_index_artifact": update.sha256,
            "inventory_count": len(CHAPTERS),
            "inventory_observed_utc_date": "2026-09-07",
            "publisher_volume_counts": volume_counts,
            "inventory_method": "All 60 native public ORS.aspx title controls expanded anonymously; 689 actual chapter hrefs, fixed seed.",
            "seed_requires_refresh_for_new_edition": True,
            "administrative_endpoints": "401; not used; no authentication or access evasion",
            "clock_caveat": "Observed UTC timestamps are acquisition clocks, not enactment or effective dates.",
        },
    )
    for chapter in CHAPTERS:
        previous = s.db.execute(
            "SELECT status FROM inventories WHERE collection_id=? AND item=?", (COLLECTION, chapter)
        ).fetchone()
        if previous is None:
            s.inventory(COLLECTION, chapter, chapter_url(chapter), "pending")
    order = sorted(CHAPTERS, key=lambda c: (c not in PRIORITY, CHAPTERS.index(c)))
    attempted = 0
    for chapter in order:
        url = chapter_url(chapter)
        if (
            not a.refresh
            and s.db.execute(
                "SELECT 1 FROM inventories i JOIN documents d ON d.id=? "
                "WHERE i.collection_id=? AND i.item=? AND i.status='indexed' "
                "AND EXISTS (SELECT 1 FROM versions v WHERE v.document_id=d.id)",
                ("ors:chapter:" + chapter.lstrip("0"), COLLECTION, chapter),
            ).fetchone()
        ):
            # Accepted older projections remain intact; completing gaps is not reindexing.
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1
        try:
            raw = a.fetch(url, max_file_bytes=12 * 1024**2)
            title, units, metadata = chapter_units(s.artifact(raw.sha256), chapter, url)
            metadata.update(
                {
                    "index_artifact": index.sha256,
                    "policy_artifact": policy.sha256,
                    "update_index_artifact": update.sha256,
                    "native_inventory_href": (
                        "http://www.oregonlegislature.gov" if chapter == "079A" else ""
                    )
                    + "/bills_laws/ors/ors"
                    + chapter
                    + ".html",
                }
            )
            _, count, new = s.ingest(
                collection=COLLECTION,
                document="ors:chapter:" + chapter.lstrip("0"),
                title=title,
                url=url,
                acquisition=raw.id,
                snapshot_date=None,
                snapshot_basis="2025 publisher edition; observed time in acquisition; not current-law or effective-date claim",
                parser="oregon-html-1",
                provisions=units,
                metadata=metadata,
            )
            s.inventory(COLLECTION, chapter, url, "indexed")
            print(
                f"oregon-ors {chapter}: {count} units ({'indexed' if new else 'unchanged'})",
                file=sys.stderr,
                flush=True,
            )
        except AcquisitionError as exc:
            s.inventory(COLLECTION, chapter, url, "failed", str(exc))
            # Do not advance to other chapter requests after publisher block or cap.
            print(f"Oregon stopped: {exc}", file=sys.stderr)
            raise
        except ValueError as exc:
            s.inventory(COLLECTION, chapter, url, "failed", str(exc))
            print(f"Oregon {chapter}: parse failed: {exc}", file=sys.stderr)


def main() -> None:
    from .sources import sync_collections

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=Path("data/collectors/oregon"))
    p.add_argument("--limit", type=int)
    args = p.parse_args()
    print(json.dumps(sync_collections(args.data, ["oregon"], limit=args.limit)))


if __name__ == "__main__":
    main()
