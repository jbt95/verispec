# VeriSpec

Desarrollo guiado por especificaciones con dientes formales: **una única spec formal**
produce (1) model checking acotado con Z3, (2) tests de propiedades generados para
cualquier lenguaje, (3) validación de trazas de implementaciones reales en cualquier
lenguaje y (4) un brief en markdown para agentes de código. La spec la posee el humano;
el agente la lee, nunca la modifica para que los tests pasen.

Basado en la arquitectura recomendada en la investigación previa (Ruta B por defecto:
verificar el modelo una vez y comprobar conformidad del código en cualquier lenguaje;
Ruta A/C donde el lenguaje lo permita). Este MVP implementa el núcleo agnóstico: el
Contract IR del informe, aquí en YAML.

```
            ┌───────────────── spec YAML (canónica, read-only para agentes) ─────────────────┐
            │ vars acotados · init · actions (requires/effects) · invariants                  │
            └──────┬──────────────┬──────────────────┬────────────────────┬───────────────────┘
                   ▼              ▼                  ▼                    ▼
            Z3 BMC (ESBMC-   export TLA+      gen-tests Python/TS   check-trace (JSON)
            style, cota K)   (TLC/Apalache)   Hypothesis/fast-check  cualquier lenguaje
                   └──────────────┴──────── brief.md (agentes) ──────┘
```

## Quickstart

```bash
cd /workspace/verispec        # o tu clon
pip install -e ".[test]"      # o: uv pip install -e ".[test]"

verispec validate examples/bank.yaml
# OK: spec Bank valid (2 vars, 2 actions, 2 invariants)

verispec check examples/bank.yaml --bound 10
# no violation up to bound 10  (bounded claim only, not a proof for all depths)

verispec check examples/bank-buggy.yaml --bound 10
# COUNTEREXAMPLE found within bound 10: invariant(s) violated: non_negative
#   step 0: state={'balance_a': 50, 'balance_b': 50}
#     -> action transfer_a_to_b params={'amount': 50}   (repite hasta agotar balance_a)
#   ... (la traza exacta la imprime el comando; es reproducible)

verispec export-tla examples/bank.yaml -o /tmp/Bank.tla
verispec gen-tests examples/bank.yaml --lang python -o /tmp/test_bank_verispec.py
verispec gen-tests examples/bank.yaml --lang typescript -o /tmp/bank.verispec.test.ts
verispec check-trace examples/bank.yaml examples/traces/bank-valid.json    # pasa
verispec check-trace examples/bank.yaml examples/traces/bank-invalid.json  # falla, step 1
verispec brief examples/bank.yaml -o /tmp/brief.md
pytest -q
```

Contrato del adaptador (cualquier lenguaje): `reset()`, `apply(action, **params)`,
`snapshot() -> dict` con exactamente las vars de la spec. Ver `docs/spec-format.md`,
`examples/bank_adapter.py` (correcto) y `examples/bank_adapter_buggy.py` (falla los
tests generados a propósito).

## Mapa a las rutas de la investigación
- **Ruta B (base, este MVP):** spec verificada una vez con Z3/TLA+ + conformidad por
  tests generados y `check-trace` — vale para cualquier lenguaje con solo un adaptador.
- **Ruta A (futura puerta CI):** ESBMC/CBMC/Kani sobre el código donde haya frontend maduro.
- **Ruta C (futura, núcleos críticos):** prueba deductiva (Verus, Aeneas→Lean, Dafny).
- **Agentes:** `brief` genera el markdown; header obligatorio *SPEC IS READ-ONLY*.

## Limitaciones honestas
- BMC **acotado**, no prueba para toda profundidad; nunca lo vendas como `proved`.
- Solo escalares acotados (`int/bool/str/enum[...]`); sin mapas/listas de estado aún.
- La corrección de la spec es responsabilidad humana: una spec mala, verificada, sigue mala.
- La exportación TLA+ se genera pero **no** se ejecuta con TLC en CI en este MVP.
- TypeScript: las expresiones se transpilan AST→TS; el esqueleto fast-check no sortea
  aún parámetros ricos por acción (documentado en el fichero generado) — Python sí.

## Roadmap
Export Lean, puertas ESBMC/Kani en CI, bucle agente↔verificador con contraejemplos
estructurados, parámetros ricos en TS, colecciones acotadas en la spec.
