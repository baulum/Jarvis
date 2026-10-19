# LiveKit / WebRTC (JNI)
-keep class livekit.org.webrtc.** { *; }
-keep class io.livekit.android.** { *; }
-dontwarn org.webrtc.**
# Porcupine wake word (JNI)
-keep class ai.picovoice.** { *; }
# Car App Library
-keep class androidx.car.app.** { *; }
# Flutter deferred components (not used)
-dontwarn com.google.android.play.core.**
