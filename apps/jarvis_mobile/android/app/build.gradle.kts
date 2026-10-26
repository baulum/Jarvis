import java.net.URI
import java.security.MessageDigest
import java.util.Properties

plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// sherpa-onnx (Apache-2.0) is not on Maven Central: fetch the release AAR once, verify the pinned hash.
val sherpaVersion = "1.13.8"
val sherpaSha256 = "633c24321e06b1fe79feafa03ea16cbc0f8a286641e2da3559bac91bdb13bd96"
val sherpaAar = file("libs/sherpa-onnx-$sherpaVersion.aar").also { aar ->
    fun sha256(f: File) = MessageDigest.getInstance("SHA-256").digest(f.readBytes()).joinToString("") { "%02x".format(it) }
    if (!aar.exists() || sha256(aar) != sherpaSha256) {
        aar.parentFile.mkdirs()
        val tmp = File(aar.parentFile, aar.name + ".part")
        logger.lifecycle("Downloading sherpa-onnx $sherpaVersion (wake word) …")
        URI("https://github.com/k2-fsa/sherpa-onnx/releases/download/v$sherpaVersion/${aar.name}").toURL()
            .openStream().use { input -> tmp.outputStream().use { input.copyTo(it) } }
        check(sha256(tmp) == sherpaSha256) { "sherpa-onnx AAR: checksum mismatch" }
        tmp.renameTo(aar)
    }
}

android {
    namespace = "systems.pje.jarvis_mobile"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        applicationId = "systems.pje.jarvis"
        minSdk = 26 // Core-Telecom
        targetSdk = 36
        // phones only (no x86 WebRTC libs); `--split-per-abi` builds use Flutter's own ABI splits instead
        if (!project.hasProperty("split-per-abi")) ndk { abiFilters += setOf("arm64-v8a", "armeabi-v7a") }
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    // Release signing: android/key.properties (not in git) – see docs/MOBILE.md › Release-Build
    val keyProps = Properties().apply {
        val f = rootProject.file("key.properties")
        if (f.exists()) f.inputStream().use { load(it) }
    }
    signingConfigs {
        if (keyProps.getProperty("storeFile") != null) {
            create("release") {
                storeFile = file(keyProps.getProperty("storeFile"))
                storePassword = keyProps.getProperty("storePassword")
                keyAlias = keyProps.getProperty("keyAlias")
                keyPassword = keyProps.getProperty("keyPassword")
            }
        }
    }

    if (keyProps.getProperty("storeFile") == null &&
        gradle.startParameter.taskNames.any { it.contains("Release", ignoreCase = true) }
    ) {
        logger.warn("WARNING: android/key.properties missing – this release build is signed with the DEBUG key (not for distribution).")
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
        debug {
            applicationIdSuffix = ".debug"
        }
    }

    packaging {
        resources.excludes += setOf("META-INF/*.kotlin_module", "META-INF/AL2.0", "META-INF/LGPL2.1")
    }

    testOptions { unitTests.isReturnDefaultValues = true }
}

dependencies {
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.core:core-telecom:1.0.1")
    implementation("androidx.car.app:app:1.7.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.10.0")
    implementation("io.livekit:livekit-android:2.29.0")
    // "Hey Jarvis" wake word, on device: sherpa-onnx keyword spotter, no account or key (see docs/MOBILE.md › Hey Jarvis)
    implementation(files(sherpaAar))
    implementation(platform("com.google.firebase:firebase-bom:35.0.2"))
    implementation("com.google.firebase:firebase-messaging")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.10.2")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20260814")
}

flutter {
    source = "../.."
}
