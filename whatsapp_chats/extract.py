"""Extract plain text from a document. Reads file bytes on stdin, prints text.

    python -I extract.py <file name>  < file

Files come from other group members, so bridge.py runs this in a separate
process with a timeout: a malformed or hostile file can hang or crash only
this process.
"""
import html
import io
import re
import sys
import zipfile

MAX_UNZIPPED = 200 * 1024 * 1024  # zip-bomb guard for docx/xlsx/pptx
TEXT_TYPES = {".txt", ".csv", ".md", ".json", ".log", ".tsv"}
SUPPORTED = TEXT_TYPES | {".pdf", ".docx", ".xlsx", ".pptx"}


def _zip(data: bytes) -> zipfile.ZipFile:
    z = zipfile.ZipFile(io.BytesIO(data))
    if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
        raise ValueError("archive expands too large")
    return z


def _xml_text(xml: str, para_tag: str, text_tag: str) -> str:
    # Regex, not an XML parser: no entity expansion on untrusted input.
    paras = re.split(rf"</{para_tag}>", xml)
    lines = ("".join(re.findall(rf"<{text_tag}(?:\s[^>]*)?>([^<]*)</{text_tag}>", p)) for p in paras)
    return "\n".join(html.unescape(line) for line in lines if line.strip())


def pdf(data: bytes) -> str:
    from pypdf import PdfReader

    return "\n\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


def docx(data: bytes) -> str:
    return _xml_text(_zip(data).read("word/document.xml").decode("utf-8", "replace"), "w:p", "w:t")


def pptx(data: bytes) -> str:
    z = _zip(data)
    slides = sorted(
        (n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
        key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()),
    )
    return "\n\n".join(
        f"[slide {i}]\n" + _xml_text(z.read(n).decode("utf-8", "replace"), "a:p", "a:t")
        for i, n in enumerate(slides, 1)
    )


def xlsx(data: bytes) -> str:
    from openpyxl import load_workbook

    _zip(data)  # size check before openpyxl unzips it
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"[sheet {ws.title}]")
        for row in ws.iter_rows(values_only=True):
            if any(v is not None for v in row):
                out.append("\t".join("" if v is None else str(v) for v in row))
    return "\n".join(out)


def extract(file_name: str, data: bytes) -> str:
    ext = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext in TEXT_TYPES:
        return data.decode("utf-8", "replace")
    handler = {".pdf": pdf, ".docx": docx, ".pptx": pptx, ".xlsx": xlsx}.get(ext)
    if handler is None:
        raise ValueError(f"unsupported file type {ext or '(none)'}")
    return handler(data)


if __name__ == "__main__":
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))  # 1 GB; not enforced on macOS
    except (ImportError, ValueError, OSError):
        pass
    sys.stdout.reconfigure(encoding="utf-8")
    print(extract(sys.argv[1], sys.stdin.buffer.read()), end="")
