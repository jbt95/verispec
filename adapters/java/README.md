# VeriSpec adapter for Java and Kotlin

This directory holds the Java side of VeriSpec:

* a **dependency free SDK** (`verispec-adapter`) with the adapter contract, the
  trace recorder and a tiny JSON writer,
* a **conformance module** (`verispec-conformance`) with the reference adapters
  for the Bank spec and the generated property test that drives them,
* the **templates** that `verispec gen-tests --lang java` (and `--lang kotlin`)
  produce, and
* an equivalent **Gradle** build for people who do not use Maven.

A VeriSpec spec is a human owned YAML file.  An adapter is the small piece
that connects a real implementation to that spec.  The generated conformance
test then drives the implementation with random actions and compares what it
reports with what the spec prescribes.

```
   bank.yaml  (the contract, human owned, read only for agents)
      |
      |  verispec gen-tests bank.yaml --lang java
      v
   BankVeriSpecTest.java  -->  your implementation  (the adapter)
      |                              |
      |                              v
      +--> jqwik drives it with random actions, compares every state
      |
      +--> TraceRecorder writes bank-trace.json
                                        |
                                        v
                       verispec check-trace bank.yaml bank-trace.json
```

---

## What was verified, and what was not

**The Java sources were compiled, and the tests were run.**  The authoring
machine turned out to have a JDK after all, just not on the `PATH`:
Homebrew's OpenJDK 27 in `/opt/homebrew/opt/openjdk`, plus a Temurin 21 and
the Kotlin 2.0.21 compiler for the Kotlin check.  Here is what was actually
executed, and what it said:

| What | How | Result |
| --- | --- | --- |
| The SDK and the tests | `javac --release 17 -Xlint:all` | compiles, no warnings |
| The test suite | JUnit Platform 1.10.2 + jqwik 1.8.4 | 24 tests, all green |
| The Java template | filled in, then compiled | compiles, no warnings |
| The generated Java test | run against `BankAdapter` | green |
| The generated Java test | run against `BankAdapterBuggy` | fails on step 0, as it must |
| The Kotlin template | filled in, then compiled with kotlinc 2.0.21 | compiles |
| The generated Kotlin test | run against `BankAdapter` | green |
| The generated Kotlin test | run against `BankAdapterBuggy` | fails on step 0, as it must |
| A recorded trace | replayed with `verispec check-trace` | `trace OK: 4 steps conform to spec Bank` |
| A broken trace | replayed with `verispec check-trace` | rejected, exit code 1 |

**What was NOT COMPILED HERE: the Maven build and the Gradle build.**
Neither `mvn` nor `gradle` is installed on the authoring machine, so
`adapters/java/pom.xml` and `adapters/java/gradle/build.gradle.kts` have
never been executed.  They declare the same coordinates the manual build used
(Java 17, JUnit 5.10.2, jqwik 1.8.4, no shading, no dependencies in the
published SDK), but **run them yourself** before you trust them:

```bash
mvn -f adapters/java/pom.xml verify
cd adapters/java/gradle && gradle test
```

The reference tooling in `tools/` is what made the manual build possible:
`render_template.py` fills the templates in (it is the executable
specification of the `{{PLACEHOLDER}}` contract) and `balance_check.py` is a
brace and paren balance checker.  Neither is needed to use the adapter.


---

## The adapter contract

An adapter is three methods:

```java
public interface VeriSpecAdapter {

    void reset();

    void apply(String action, Map<String, Object> params);

    Map<String, Object> snapshot();
}
```

| Method | Meaning |
| --- | --- |
| `reset()` | Put the implementation into the state the spec's `init` block prescribes. |
| `apply(action, params)` | Apply exactly one action of the spec. |
| `snapshot()` | The current state, with **exactly** the spec's state variables. |

Rules that the generated test relies on:

* The action names and the parameter names are the ones the spec spells.
* `snapshot()` returns **exactly** the spec's variables, no more and no fewer.
  A missing or an extra variable is a failure, not a detail.
* `apply()` is only called when the action's preconditions hold, so an
  implementation may (and should) reject an action whose precondition does not
  hold.
* An unknown action is a programming error in the adapter: throw
  `IllegalArgumentException`.  A violated precondition is a bug in the
  implementation: throw `IllegalStateException`.

### How the state is represented

The state is a `Map<String, Object>` with one entry per spec variable:

| Spec type | Java type | JSON |
| --- | --- | --- |
| `int` | `Integer` | number |
| `bool` | `Boolean` | `true` / `false` |
| `str` | `String` | string |
| `enum[a,b]` | `String` | string |
| `map` | `Map<String, Object>` | object, keys stringified (`"0"` for `0`) |
| `set` | `Set<Object>` | array (order is not significant) |
| `seq` | `List<Object>` | array (order is significant) |
| `record` | `Map<String, Object>` | object, keyed by field name |

In Kotlin the same map is `Map<String, Any?>`; the parameters of an action are
`Map<String, Any>`.

### The value semantics

`VeriSpecValues` implements the edge cases of the spec language, so that the
Java side and the Python/Z3 side agree on them:

| Rule | What it means | Helper |
| --- | --- | --- |
| A missing map key reads as the value type default | `0`, `false`, `""` or the first enum value, never an error | `mapGet` |
| A `map_set` with a key outside the declared domain is ignored | the map comes back unchanged | `mapSet` |
| A set never holds a duplicate | adding an element that is already there changes nothing | `setAdd` |
| A set element outside the declared domain is ignored | the set comes back unchanged | `setAdd` |
| A sequence keeps its order and its duplicates | `seq_append` grows the tail | `seqAppend` |
| An out of range sequence index reads as the element default | `seq_get(q, 99)` is the default, not an error | `seqGet` |
| Numbers compare by value | `50` and `50L` are the same number; a boolean is never a number | `equal` |
| Enum values are strings | `status == "pending"` compares the string | - |

Map keys are strings on the wire: the integer key `3` is written and read as
`"3"` (`VeriSpecValues.key`).

---

## The trace recorder

`TraceRecorder` records what an adapter did and writes it in the canonical
VeriSpec trace format: a JSON list of steps whose first step is the initial
state.

```java
VeriSpecAdapter adapter = new BankAdapter();
TraceRecorder recorder = TraceRecorder.start(adapter);      // reset() + the init step

adapter.apply("transfer_a_to_b", Map.of("amount", 20));
recorder.recordApplied(adapter, "transfer_a_to_b", Map.of("amount", 20));

recorder.writeTo(Path.of("bank-trace.json"));
```

The file it writes:

```json
[
  {"action": "init", "params": {}, "state": {"balance_a": 50, "balance_b": 50}},
  {"action": "transfer_a_to_b", "params": {"amount": 20},
   "state": {"balance_a": 30, "balance_b": 70}}
]
```

Every step has an `action`, a `params` object and a `state` object with
exactly the spec's variables.  The first step is always `init`.

Replay it against the spec with the language agnostic bridge:

```bash
verispec check-trace bank.yaml bank-trace.json
```

`Json` reads the same format back (`Json.parseTrace`), and `TraceRecorder`
reads a trace file into steps (`TraceRecorder.readTrace`).

---

## Generating the conformance test

```bash
verispec gen-tests bank.yaml --lang java -o src/test/java/verispec/conformance/BankVeriSpecTest.java
verispec gen-tests bank.yaml --lang kotlin -o src/test/java/verispec/conformance/BankVeriSpecTest.kt
```

The templates live in `templates/`:

| File | What it produces |
| --- | --- |
| `templates/conformance.java.txt` | a Java class with a jqwik property test |
| `templates/conformance.kt.txt` | the Kotlin version of the same test |

Both are filled in from the spec.  The placeholders are:

| Placeholder | Filled with |
| --- | --- |
| `{{PACKAGE}}` | the package of the generated test |
| `{{CLASS_NAME}}` | the name of the generated test class |
| `{{STATE_FIELDS}}` | the initial state and the accessors of the state variables |
| `{{ACTION_FUNCTIONS}}` | the preconditions and the effects of every action, plus the two dispatchers |
| `{{INVARIANT_FUNCTIONS}}` | the invariant checks |
| `{{ADAPTER_FACTORY}}` | the factory that builds the implementation under test |
| `{{PARAM_STRATEGIES}}` | the strategies that draw the random actions and their parameters |

The generator itself lives in `src/verispec/gentests.py`, outside this
directory.  What this directory fixes is the contract it has to satisfy: each
placeholder sits on a line of its own and is replaced by a block of code, and
the generated blocks must provide the members listed in the header of the
template (`SPEC_INIT`, `requires`, `effects`, `checkInvariants`,
`newAdapter` and `steps`).

Everything except the adapter factory is generated and **must not be edited by
hand**: regenerate the file instead.  The adapter factory is the one seam, and
it is marked `EDIT ME` in the generated file.  Its default implementation
reads the class name of the implementation under test from the
`verispec.adapter` system property or the `VERISPEC_ADAPTER` environment
variable:

```bash
mvn test -Dverispec.adapter=com.example.BankAdapter
```

so the generated file compiles and runs before you have written anything.

### What the generated test does

1. `reset()` the adapter and check that the state is exactly the spec's `init`.
2. Draw an action and its parameters.  The parameters are drawn inside the
   range the spec declares, so the test never explores a value the spec does
   not allow.
3. If the action's preconditions do not hold in the current state, **skip** the
   action: an action that is not enabled cannot be taken.
4. Apply the action and compare the adapter's snapshot with the state the
   spec's `effects` prescribe.
5. Check every invariant of the spec against the new state, and against the
   initial state.

`tools/render_template.py` is a reference implementation of that filling.  It
is not the generator (that one lives in `src/verispec/gentests.py`), but it is
the executable specification of the placeholder contract, and it is what was
used to check that both templates compile:

```bash
python adapters/java/tools/render_template.py examples/bank.yaml \
    --lang java -o /tmp/BankVeriSpecTest.java
python adapters/java/tools/render_template.py examples/bank.yaml \
    --lang kotlin -o /tmp/BankVeriSpecTest.kt
```

It handles the scalar subset of the spec language and refuses anything else
with a clear error, so it is a check of the templates, not a second
generator.

---

## Running it

### Maven

The build has two modules: `verispec-adapter` (the SDK) and
`verispec-conformance` (the adapters and the generated test).

```bash
# everything
mvn -f adapters/java/pom.xml verify

# just the SDK and its unit tests
mvn -f adapters/java/verispec-adapter/pom.xml test

# just the conformance tests
mvn -f adapters/java/verispec-conformance/pom.xml test
```

The SDK jar has **no dependencies**: the adapter contract, the trace recorder
and the JSON writer are plain Java 17, and nothing is shaded, so dropping the
jar into a project does not drag a JSON library into the build.  JUnit 5 and
jqwik are test scoped only.

The conformance module needs the SDK on its classpath; the aggregator
`adapters/java/pom.xml` puts it there for you.  If you build the conformance
module on its own, install the SDK first:

```bash
mvn -f adapters/java/verispec-adapter/pom.xml install
```

### Gradle

The equivalent Gradle build lives in `adapters/java/gradle/` and drives the
same sources, so there is only ever one copy of the Java code.

```bash
cd adapters/java/gradle

gradle test                              # everything
gradle :verispec-adapter:test            # the SDK tests
gradle :verispec-conformance:test        # the conformance tests
```

For the Kotlin variant of the generated test, add the Kotlin plugin to
`adapters/java/gradle/build.gradle.kts`:

```kotlin
plugins {
    kotlin("jvm") version "1.9.24"
}
```

and apply it to the conformance project.

### In your own project

Add the SDK as a test dependency and let the generator write the test:

```xml
<dependency>
  <groupId>dev.verispec</groupId>
  <artifactId>verispec-adapter</artifactId>
  <version>0.1.0</version>
  <scope>test</scope>
</dependency>
<dependency>
  <groupId>net.jqwik</groupId>
  <artifactId>jqwik</artifactId>
  <version>1.8.4</version>
  <scope>test</scope>
</dependency>
```

Your adapter implements `dev.verispec.VeriSpecAdapter`, and the generated test
finds it through the `verispec.adapter` property or by editing the one
`EDIT ME` line.

---

## What a green run means

**A green conformance run is `conformance-tested` evidence.  It is not a
proof.**

| | |
| --- | --- |
| Guarantee | `conformance-tested` |
| What it says | the implementation matched the spec on every sequence of actions that was tried |
| What it does not say | that the implementation matches the spec on every possible sequence |

The guarantee label is the one the rest of VeriSpec uses
(`dev.verispec.TraceRecorder.GUARANTEE` and
`dev.verispec.VeriSpecAdapter.GUARANTEE`).  The bounded model checker has its
own, weaker guarantee, `bounded-proved(k)`, and it is a different claim: "no
violation up to depth k", not "no violation at all".

The conformance test is strong evidence and it is cheap, but it is a sample of
the behaviour space, not the behaviour space itself.

---

## Layout

```
adapters/java/
├── README.md                       this file
├── pom.xml                         the Maven aggregator
├── verispec-adapter/               the dependency free SDK
│   ├── pom.xml
│   └── src/
│       ├── main/java/dev/verispec/
│       │   ├── VeriSpecAdapter.java  the adapter contract
│       │   ├── TraceRecorder.java    the trace recorder
│       │   ├── Json.java             the tiny JSON writer and reader
│       │   └── VeriSpecValues.java   the collection semantics
│       └── test/java/dev/verispec/
│           └── TraceRecorderTest.java
├── verispec-conformance/           the reference adapters and the test
│   ├── pom.xml
│   └── src/test/java/dev/verispec/conformance/
│       ├── BankAdapter.java          the correct implementation
│       ├── BankAdapterBuggy.java     the one that leaks a unit
│       ├── BankVeriSpecTest.java     the jqwik property test
│       ├── BankAdapterBuggyTest.java the bug is caught
│       └── BankTraceTest.java        the trace recorder in action
├── templates/
│   ├── conformance.java.txt       what `gen-tests --lang java` produces
│   └── conformance.kt.txt         what `gen-tests --lang kotlin` produces
├── gradle/                        the equivalent Gradle build
│   ├── settings.gradle.kts
│   └── build.gradle.kts
└── tools/                         reference tooling, not needed to use it
    ├── render_template.py         fills the templates in, so they can be checked
    └── balance_check.py           brace and paren balance checker
```

The `BankAdapterBuggy` adapter loses one unit on every transfer.  It is there
so that the conformance test can be shown to catch something: a test that
never fails is not evidence of anything.
