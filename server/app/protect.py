import re

HANGUL = re.compile(r"[가-힣]")
PH = "⟦C{}⟧"
PH_RE = re.compile(r"⟦C\d+⟧")

CODE_LINE = re.compile(
    r"""^\s*(
        (def|class|import|from|return|public|private|protected|static|function|const|let|var|
         if\s*\(|for\s*\(|while\s*\(|try|catch|\#include|package|echo|print\()\b
      | (SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)\s
      | </?\w+[^>]*>
      | .*[;{}]\s*$
      | .*(==|!=|=>|->|::|&&|\|\|)
    )""",
    re.VERBOSE,
)

INLINE = re.compile(
    r"""(
        `[^`\n]+`                                   # `code`
      | https?://[!-~]+                              # URL (ASCII까지만)
      | \b[A-Za-z_][\w.]*\([^()\n]*\)               # func(), obj.method(x)
      | \b[a-z]+[A-Z]\w*\b                          # camelCase
      | \b[A-Z][a-z]+[A-Z]\w*\b                     # PascalCase 복합어
      | \b[a-z]+_[a-z_\d]+\b                        # snake_case
      | (?:[\w-]+/)+[\w.-]+\.\w{1,5}\b              # 경로/파일.확장자
    )""",
    re.VERBOSE | re.ASCII,  # \b, \w가 한글 조사를 삼키지 않게
)


def protect(text: str) -> tuple[str, list[str]]:
    """코드로 보이는 줄·토큰을 자리표시로 치환"""
    saved: list[str] = []

    def keep(s: str) -> str:
        saved.append(s)
        return PH.format(len(saved))

    lines = []
    for line in text.split("\n"):
        if line.strip() and not HANGUL.search(line) and CODE_LINE.match(line):
            lines.append(keep(line))
        else:
            lines.append(INLINE.sub(lambda m: keep(m.group(0)), line))
    return "\n".join(lines), saved


def restore(text: str, saved: list[str]) -> str:
    for i, s in enumerate(saved, 1):
        text = text.replace(PH.format(i), s)
    return text


def check(text: str, saved: list[str]) -> list[str]:
    issues = []
    for i in range(1, len(saved) + 1):
        n = text.count(PH.format(i))
        if n != 1:
            issues.append(f"코드 자리표시 {PH.format(i)} {'누락' if n == 0 else '중복'}")
    return issues
