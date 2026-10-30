"""확장 프로그램 없이 로컬에서 테스트
python -m app.cli translate 교안.pdf --lang ru --course secure-coding
python -m app.cli candidates 교안.pdf --no-llm > glossary/new-course.json
"""
import argparse
import json
from pathlib import Path

from .settings import load_environment

load_environment()

from . import candidates as cand
from . import service
from .llm import OpenAICompatible
from .pdf import extract_pages


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("translate")
    t.add_argument("pdf")
    t.add_argument("--lang", required=True)
    t.add_argument("--course")
    c = sub.add_parser("candidates")
    c.add_argument("pdf")
    c.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()

    data = Path(a.pdf).read_bytes()
    if a.cmd == "translate":
        out = service.translate_pdf(data, a.lang, a.course, OpenAICompatible())
    else:
        pages = extract_pages(data)
        lists = [cand.regex_candidates(pages)]
        if not a.no_llm:
            lists.insert(0, cand.llm_candidates(pages, OpenAICompatible()))
        out = {"terms": cand.merge(*lists)}
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
