import argparse, sys
from pathlib import Path
from .model import load_spec
from .expr import SpecError
def main(argv=None):
    p=argparse.ArgumentParser(prog="verispec", description="One formal spec -> Z3 BMC, TLA+, generated tests, trace conformance.")
    sub=p.add_subparsers(dest="cmd", required=True)
    sp=sub.add_parser("validate"); sp.add_argument("spec")
    sp=sub.add_parser("check"); sp.add_argument("spec"); sp.add_argument("--bound",type=int,default=10); sp.add_argument("--invariant")
    sp=sub.add_parser("export-tla"); sp.add_argument("spec"); sp.add_argument("-o","--output",required=True)
    sp=sub.add_parser("gen-tests"); sp.add_argument("spec"); sp.add_argument("--lang",choices=["python","typescript"],required=True); sp.add_argument("-o","--output",required=True)
    sp=sub.add_parser("check-trace"); sp.add_argument("spec"); sp.add_argument("trace")
    sp=sub.add_parser("brief"); sp.add_argument("spec"); sp.add_argument("-o","--output")
    args=p.parse_args(argv)
    try:
        spec=load_spec(args.spec)
        if args.cmd=="validate":
            print(f"OK: spec {spec.name} valid ({len(spec.vars)} vars, {len(spec.actions)} actions, {len(spec.invariants)} invariants)"); return 0
        if args.cmd=="check":
            from .bmc import check, format_result
            r=check(spec,bound=args.bound,invariant_filter=args.invariant); print(format_result(spec,r)); return 0 if r["status"]=="ok" else 1
        if args.cmd=="export-tla":
            from .tla import export_tla
            Path(args.output).write_text(export_tla(spec)); print(f"wrote TLA+ module to {args.output} (not run through TLC in this MVP)"); return 0
        if args.cmd=="gen-tests":
            from .gentests import gen_python, gen_typescript
            out=gen_python(spec) if args.lang=="python" else gen_typescript(spec)
            Path(args.output).write_text(out); print(f"wrote {args.lang} tests to {args.output}"); return 0
        if args.cmd=="check-trace":
            from .trace import check_trace
            ok,msg=check_trace(spec,args.trace); print(msg); return 0 if ok else 1
        if args.cmd=="brief":
            from .brief import make_brief
            txt=make_brief(spec)
            if args.output: Path(args.output).write_text(txt); print(f"wrote brief to {args.output}")
            else: print(txt)
            return 0
    except SpecError as e:
        print(f"error: {e}", file=sys.stderr); return 2
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr); return 2
if __name__=="__main__": sys.exit(main())
