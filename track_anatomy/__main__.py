"""Command line:  python -m track_anatomy <command>

  serve                     run the web app (same as ./run.sh)
  analyze FILE [FILE ...]   analyse audio files and print a short summary of each
  list                      list the library
  export TRACK_ID FOLDER    write an Ableton project folder (.als, stems, MIDI, guide)
"""
import argparse
import sys
from pathlib import Path

from . import config, tracks


def _summary_line(tid: str) -> str:
    s = tracks.summary(tid) or {}
    bpm = "no beat" if s.get("has_beat") is False else (f"{s['bpm']:.1f} BPM" if s.get("bpm") else "-")
    key = s.get("key") or "key unclear"
    bars = f"{s['bars']} bars" if s.get("bars") else ""
    return f"{tid:48s} {s.get('state', '?'):8s} {bpm:>10s}  {key:10s} {bars}"


def cmd_serve(a):
    import uvicorn
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: listening on {a.host}. Track Anatomy has no login: anyone who can reach this port can "
              "use and delete your library. Requests are only answered for host names listed in "
              "TRACK_ANATOMY_ALLOWED_HOSTS.", file=sys.stderr, flush=True)
    print(f"Track Anatomy {config.__version__}: http://localhost:{a.port}", flush=True)
    uvicorn.run("track_anatomy.server:app", host=a.host, port=a.port)


def cmd_analyze(a):
    from .pipeline.run import run
    for f in a.files:
        p = Path(f)
        if not p.is_file():
            print(f"skip {f}: not a file", file=sys.stderr)
            continue
        err = tracks.validate(p)
        if err:
            print(f"skip {f}: {err}", file=sys.stderr)
            continue
        tid = tracks.create(p, p.name)
        d = tracks.path(tid)
        if (d / "analysis.json").exists() and not a.force:
            print(f"{tid}: already analysed (use --force to redo)")
        else:
            print(f"{tid}: analysing {p.name} ...", flush=True)
            try:
                run(d, "analyze" if a.force and (d / "cache").exists() else None)
            except SystemExit:
                st = tracks.read_json(d / "status.json", {}) or {}
                print(f"{tid}: failed: {st.get('error')}", file=sys.stderr)
                continue
        print(_summary_line(tid))


def cmd_list(a):
    for tid in tracks.all_ids():
        print(_summary_line(tid))


def cmd_export(a):
    from .pipeline import export as EX
    d = tracks.path(a.track_id)
    A = tracks.read_json(d / "analysis.json")
    if not A:
        sys.exit(f"{a.track_id}: not analysed")
    out = Path(a.folder).expanduser() / f"{EX.safe_name(A['track']['title'])} Project"
    als = EX.build_project(d, A, out)
    print(als or out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="track_anatomy", description="Track Anatomy " + config.__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default=config.HOST)
    s.add_argument("--port", type=int, default=config.PORT)
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("analyze", help="analyse audio files")
    s.add_argument("files", nargs="+")
    s.add_argument("--force", action="store_true", help="re-run the analysis step for files already done")
    s.set_defaults(fn=cmd_analyze)
    s = sub.add_parser("list", help="list the library")
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser("export", help="write an Ableton project folder")
    s.add_argument("track_id")
    s.add_argument("folder")
    s.set_defaults(fn=cmd_export)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
