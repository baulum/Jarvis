import java.util.Properties

plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
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
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.core:core-telecom:1.0.1")
    implementation("androidx.car.app:app:1.7.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.10.0")
    implementation("io.livekit:livekit-android:2.29.0")
    // "Hey Jarvis" wake word, on device (needs a Picovoice AccessKey – see docs/MOBILE.md › Hey Jarvis)
    implementation("ai.picovoice:porcupine-android:3.0.2")
    implementation(platform("com.google.firebase:firebase-bom:35.0.2"))
    implementation("com.google.firebase:firebase-messaging")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.10.2")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")
}

flutter {
    source = "../.."
}
