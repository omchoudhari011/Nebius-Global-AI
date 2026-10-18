"""Searching inside your files: what the desktop app reads, what it skips and what it sends."""

import zipfile
from pathlib import Path

from custodian_desktop import indexer


def _zip(path: Path, parts: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return path


def _pdf(path: Path, text: str) -> Path:
    """A minimal one-page PDF with `text` on it."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def test_text_comes_out_of_office_files_pdfs_and_plain_text(tmp_path):
    docx = _zip(tmp_path / "lease.docx", {"word/document.xml": "<w:document><w:p><w:t>Rent is 18,000</w:t></w:p>"
                                                               "<w:p><w:t>Deposit one lakh</w:t></w:p></w:document>"})
    pptx = _zip(tmp_path / "plan.pptx", {"ppt/slides/slide2.xml": "<a:p><a:t>Second slide</a:t></a:p>",
                                         "ppt/slides/slide1.xml": "<a:p><a:t>First slide</a:t></a:p>"})
    xlsx = _zip(tmp_path / "budget.xlsx", {"xl/sharedStrings.xml": "<sst><si><t>Groceries</t></si><si><t>Rent</t></si></sst>"})
    pdf = _pdf(tmp_path / "bill.pdf", "Electricity bill 1840 rupees")
    note = tmp_path / "note.md"
    note.write_text("# Trip\nGoa in December", encoding="utf-8")
    assert indexer.extract(docx) == "Rent is 18,000\nDeposit one lakh"
    assert indexer.extract(pptx) == "First slide\nSecond slide"
    assert indexer.extract(xlsx) == "Groceries Rent"
    assert "Electricity bill 1840 rupees" in indexer.extract(pdf)
    assert indexer.extract(note) == "# Trip\nGoa in December"
    (tmp_path / "broken.docx").write_bytes(b"not a zip")
    assert indexer.extract(tmp_path / "broken.docx") == ""  # unreadable: skipped, not an error


def test_long_text_is_cut_into_overlapping_passages_at_sentence_ends():
    text = " ".join(f"Sentence number {i} is here." for i in range(200))
    pieces = indexer.chunks(text)
    assert all(len(p) <= indexer.CHUNK_CHARS for p in pieces) and len(pieces) > 3
    assert all(p.endswith(".") for p in pieces[:-1])
    assert pieces[1][:20] in pieces[0]  # a little overlap, so nothing is split away from its context


def test_secrets_system_folders_and_unreadable_files_are_skipped(tmp_path):
    (tmp_path / "taxes.pdf").write_bytes(b"%PDF")
    (tmp_path / "passwords.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".env").write_text("x", encoding="utf-8")
    (tmp_path / "id_rsa").write_text("x", encoding="utf-8")
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8")
    (tmp_path / "~$draft.docx").write_bytes(b"x")  # Word's lock file
    for skipped in ("node_modules", ".git", "venv", "recovery codes"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "readme.md").write_text("x", encoding="utf-8")
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "plan.md").write_text("x", encoding="utf-8")
    found = indexer.walk([str(tmp_path)], limit=100)
    assert sorted(Path(p).name for p in found) == ["plan.md", "taxes.pdf"]


def test_only_new_or_changed_files_are_sent_and_gone_ones_forgotten():
    on_disk = {"a.txt": (10.0, 5), "b.txt": (20.0, 7), "c.txt": (30.0, 9)}
    indexed = {"a.txt": [10.0, 5], "b.txt": [15.0, 7], "old.txt": [1.0, 1]}
    assert indexer.plan(on_disk, indexed) == (["b.txt", "c.txt"], ["old.txt"])


def test_one_pass_sends_the_text_in_batches(tmp_path):
    for i in range(5):
        (tmp_path / f"note{i}.txt").write_text(f"note number {i}", encoding="utf-8")
    sent = []

    class Client:
        def index_config(self):
            return {"enabled": True, "folders": [str(tmp_path)], "indexed": {str(tmp_path / "gone.txt"): [1, 1]}, "batch": 2}

        def index_files(self, files, removed):
            sent.append(([f["name"] for f in files], removed))
            return {}

    result = indexer.Indexer(lambda: Client()).once()
    assert result == {"enabled": True, "files": 5, "sent": 5}
    assert [len(names) for names, _ in sent] == [2, 2, 1]
    assert sent[0][1] == [str(tmp_path / "gone.txt")] and sent[1][1] == []
    assert indexer.Indexer(lambda: type("Off", (), {"index_config": lambda self: {"enabled": False}})()).once() == {"enabled": False}
