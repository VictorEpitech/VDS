"""Processus jetable : rend un PDF en JPEG. Lancé par module.py avec `python -I`, env vide.

Usage : render_worker.py <pdf> <outdir> <width> <max_pages> <max_height> <mem_bytes> <cpu_s>
Codes de sortie : 0 ok (JSON sur stdout), 3 invalide, 4 trop de pages, autre = échec.
"""

import io
import json
import resource
import sys

EXIT_INVALID = 3
EXIT_TOO_MANY = 4


def main(argv: list[str]) -> int:
    pdf_path, outdir = argv[0], argv[1]
    width, max_pages, max_height, mem, cpu = (int(v) for v in argv[2:7])
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(pdf_path)
    except pdfium.PdfiumError:
        return EXIT_INVALID
    count = len(pdf)
    if count == 0:
        return EXIT_INVALID
    if count > max_pages:
        return EXIT_TOO_MANY
    first = (0, 0)
    try:
        for index in range(count):
            page = pdf[index]
            w, h = page.get_size()
            if w <= 0 or h <= 0:
                return EXIT_INVALID
            scale = min(width / w, max_height / h)
            image = page.render(scale=scale).to_pil().convert("RGB")
            out = io.BytesIO()
            image.save(out, "JPEG", quality=85)
            with open(f"{outdir}/page-{index + 1}.jpg", "wb") as fh:
                fh.write(out.getvalue())
            if index == 0:
                first = (image.width, image.height)
            page.close()
    except pdfium.PdfiumError:
        return EXIT_INVALID
    sys.stdout.write(json.dumps({"count": count, "width": first[0], "height": first[1]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
