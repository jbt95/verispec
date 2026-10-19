// The VeriSpec Java adapter, built with Gradle.
//
// This is the equivalent of adapters/java/pom.xml: the same sources, the same
// dependencies, the same Java 17 baseline.
//
//     gradle test                                  # everything
//     gradle :verispec-adapter:test                # the SDK tests
//     gradle :verispec-conformance:test            # the conformance tests
//
// The SDK itself has no dependencies at all: the adapter contract, the trace
// recorder and the JSON writer are plain Java 17, and nothing is shaded.

plugins {
    java
}

allprojects {
    group = "dev.verispec"
    version = "0.1.0"

    repositories {
        mavenCentral()
    }
}

subprojects {
    apply(plugin = "java")

    // Java 17 is the baseline of the VeriSpec Java adapter.  Prefer a
    // toolchain if you want Gradle to provision the JDK for you:
    //
    //     java { toolchain { languageVersion.set(JavaLanguageVersion.of(17)) } }
    java {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    dependencies {
        "testImplementation"("org.junit.jupiter:junit-jupiter:5.10.2")
        "testImplementation"("net.jqwik:jqwik:1.8.4")
        "testRuntimeOnly"("org.junit.platform:junit-platform-launcher")
    }

    tasks.withType<Test>().configureEach {
        useJUnitPlatform()
    }
}

// The conformance module drives the adapter through the SDK.
project(":verispec-conformance") {
    dependencies {
        "testImplementation"(project(":verispec-adapter"))
    }
}
