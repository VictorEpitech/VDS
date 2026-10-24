plugins {
    id("org.jetbrains.kotlin.jvm")
}

kotlin { jvmToolchain(17) }

dependencies {
    api("io.ktor:ktor-server-core:2.3.12")
    api("io.ktor:ktor-server-cio:2.3.12")
    implementation("io.ktor:ktor-websockets:2.3.12")
    api("com.squareup.okhttp3:okhttp:4.12.0")
    runtimeOnly("org.slf4j:slf4j-nop:2.0.13")
    testImplementation("junit:junit:4.13.2")
    testImplementation(kotlin("test-junit"))
}

tasks.test {
    useJUnit()
    testLogging { events("failed", "passed") }
}
