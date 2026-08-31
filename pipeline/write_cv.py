"""CLI: turn output/cv_context.json into output/structured_cv.json.

    output/cv_context.json --(this script)--> output/structured_cv.json --(render_cv.py)--> Markdown

Two modes, matching cv_writer.CVWriter's two implementations:

  Deterministic (default):
      python write_cv.py output/cv_context.json
  Writes structured_cv.json (next to the input file) using
  cv_writer.DeterministicCVWriter -- rule-based selection/merging/bucketing,
  no LLM call, no prose rewriting.

  Claude-assisted:
      1. Run this script (or have it already run) to produce output/cv_context.json.
      2. Have Claude Code read output/cv_context.json and structured_cv.StructuredCV,
         and hand-author a better-written structured_cv.json against that exact
         schema (tighter bullets, a sharper profile paragraph -- the prose
         quality the deterministic mode deliberately does not attempt).
      3. Validate it before use:
             python write_cv.py output/cv_context.json --validate-only output/structured_cv.json
         (or just let render_cv.py load it -- ManualStructuredCVWriter validates
         via Pydantic on load, so a malformed hand-authored file fails loudly
         instead of silently rendering something wrong.)

Neither mode calls Neo4j or a live LLM API. Both are read-only with respect to
cv_context.json.
"""
import sys
from pathlib import Path

from cv_writer import DeterministicCVWriter, ManualStructuredCVWriter
from tailor_cv import CVContext


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 1

    context_path = Path(args[0])
    if not context_path.exists():
        print(f"ERROR: {context_path} not found", file=sys.stderr)
        return 1

    cv_context = CVContext.model_validate_json(context_path.read_text(encoding="utf-8"))

    if "--validate-only" in args:
        idx = args.index("--validate-only")
        structured_path = Path(args[idx + 1])
        writer = ManualStructuredCVWriter(structured_path)
        structured = writer.write(cv_context)
        print(f"{structured_path} is valid StructuredCV JSON.")
        print(f"  experience={len(structured.experience)} projects={len(structured.projects)} "
              f"education={len(structured.education)} languages={structured.languages}")
        return 0

    writer = DeterministicCVWriter()
    structured = writer.write(cv_context)

    out_path = context_path.parent / "structured_cv.json"
    out_path.write_text(
        structured.model_dump_json(indent=2, exclude_none=False), encoding="utf-8"
    )
    print(f"Wrote {out_path} (deterministic mode)")
    print(f"  experience={len(structured.experience)} projects={len(structured.projects)} "
          f"education={len(structured.education)} languages={structured.languages}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
