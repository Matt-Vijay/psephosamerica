import io
import json
import zipfile

import pytest
from conftest import retain

from psephos.collect_california import FIELDS, import_archive, lob_bytes, mysql_rows


def test_mysql_tab_escaping_null_and_quoted_delimiters():
    source = "`inline\ttab`\t`line\nfeed`\t`escaped\\nline`\t\\N\tNULL\t`NULL`\t`two``ticks`\t`slash\\\\end`\n"
    assert list(mysql_rows(io.StringIO(source))) == [
        [
            "inline\ttab",
            "line\nfeed",
            "escaped\nline",
            None,
            None,
            "NULL",
            "two`ticks",
            "slash\\end",
        ]
    ]
    assert list(mysql_rows(["`escaped\\`tick`\tlast"])) == [["escaped`tick", "last"]]
    with pytest.raises(ValueError, match="Truncated"):
        list(mysql_rows(["`unclosed"]))


def test_pubinfo_full_import_preserves_versions_lobs_context_and_clocks(store):
    def dump(table, rows):
        def field(value):
            if value is None:
                return "\\N"
            return "`" + str(value).replace("\\", "\\\\").replace("`", "\\`") + "`"

        return "".join("\t".join(field(r.get(f)) for f in FIELDS[table]) + "\n" for r in rows)

    sections = [
        {
            "ID": f"id-{v}",
            "LAW_CODE": "GOV",
            "SECTION_NUM": "10248.",
            "LAW_SECTION_VERSION_ID": v,
            "CONTENT_XML": f"LAW_SECTION_TBL_{i}.lob",
            "HISTORY": "Added by Stats. 1993.",
            "EFFECTIVE_DATE": "2027-01-01 00:00:00",
            "ACTIVE_FLG": "Y",
            "DIVISION": "2",
            "OP_STATUES": "1993",
        }
        for i, v in enumerate(["native-old", "native-new"])
    ]
    toc = [
        {
            "ID": r["ID"],
            "LAW_CODE": "GOV",
            "LAW_SECTION_VERSION_ID": r["LAW_SECTION_VERSION_ID"],
            "NODE_TREEPATH": "0.1",
            "TITLE": "Public access",
            "SECTION_ORDER": str(2 - i),
            "SEQ_NUM": "1",
        }
        for i, r in enumerate(sections)
    ]
    nodes = [
        {
            "LAW_CODE": "GOV",
            "DIVISION": "2",
            "HEADING": "General provisions",
            "NODE_TREEPATH": "0.1",
            "NODE_SEQUENCE": "1",
        }
    ]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        for table, rows in zip(
            FIELDS,
            [sections, toc, nodes, [{"CODE": "GOV", "TITLE": "Government Code"}]],
            strict=True,
        ):
            z.writestr(table + ".dat", dump(table, rows))
        for r in sections:
            z.writestr(
                r["CONTENT_XML"],
                '<section xmlns:caml="http://lc.ca.gov/legalservices/schemas/caml.1#"><p>Access <em>shall</em> remain free.</p><p>(a)<span class="EnSpace"/>Half: <caml:Fraction><caml:Numerator>1</caml:Numerator><caml:Denominator>2</caml:Denominator></caml:Fraction></p><table><tr><td>A</td><td>B</td></tr></table><img src="figure.png"/><caml:TipIn numPages="3"/></section>',
            )
        with pytest.raises(ValueError, match="Unsafe"):
            lob_bytes(z, "../../secret")
    receipt = retain(store, output.getvalue())
    store.db.execute(
        "UPDATE acquisitions SET headers=? WHERE id=?",
        (json.dumps({"last-modified": "Sun, 30 Aug 2026 21:25:00 GMT"}), receipt.id),
    )
    store.db.commit()
    report = import_archive(store, receipt)
    assert report["section_distinct_citations"] == 1
    assert report["section_distinct_native_versions"] == 2
    assert report["snapshot_date"] == "2026-08-30"
    provisions = store.db.execute("SELECT * FROM provisions ORDER BY ordinal").fetchall()
    assert len(provisions) == 2
    assert "native-new" in provisions[0]["key"]
    assert "A\tB" in provisions[0]["text"]
    assert "not transcribed" in provisions[0]["text"]
    assert "(a) Half: 1/2" in provisions[0]["text"]
    assert "TipIn (3 pages)" in provisions[0]["text"]
    metadata = json.loads(provisions[0]["metadata"])
    assert metadata["source_fields"]["OP_STATUES"] == "1993"
    assert metadata["effective_on"] == "2027-01-01 00:00:00"
    assert metadata["hierarchy"][0]["HEADING"] == "General provisions"
    assert metadata["media"][0]["source_locator"] == "figure.png"
    assert metadata["media"][1]["url"] == ""
    assert import_archive(store, receipt)["per_code"]["GOV"]["inserted"] is False
