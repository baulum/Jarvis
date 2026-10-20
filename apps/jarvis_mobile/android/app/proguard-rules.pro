# LiveKit / WebRTC (JNI)
-keep class livekit.org.webrtc.** { *; }
-keep class io.livekit.android.** { *; }
-dontwarn org.webrtc.**
# sherpa-onnx wake word (JNI looks up these classes and fields by name)
-keep class com.k2fsa.sherpa.onnx.** { *; }
# Car App Library
-keep class androidx.car.app.** { *; }
# Flutter deferred components (not used)
-dontwarn com.google.android.play.core.**
