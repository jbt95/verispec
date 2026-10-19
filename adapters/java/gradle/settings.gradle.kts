// The Gradle view of the VeriSpec Java adapter build.
//
// It drives exactly the same sources as the Maven build one directory up, so
// there is only ever one copy of the Java code.  Run it from this directory:
//
//     gradle test
//     gradle :verispec-conformance:test
//
// The Kotlin variant of the conformance test needs the Kotlin plugin; see the
// README for the one line to add.

rootProject.name = "verispec-adapter-build"

include("verispec-adapter")
include("verispec-conformance")

// The Maven modules live next to this file, not inside it.
project(":verispec-adapter").projectDir = file("../verispec-adapter")
project(":verispec-conformance").projectDir = file("../verispec-conformance")
